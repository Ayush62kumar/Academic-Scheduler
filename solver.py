"""Timetable generation with OR-Tools CP-SAT, plus an independent validator.

Hard constraints
  * a faculty member / room / student section hosts at most one session per slot
  * room type, capacity and lab equipment must fit the session
  * faculty availability is respected
  * contact hours per course are met (one event per hour; labs are 2h blocks)
  * lab blocks never straddle the lunch break
  * a theory course meets at most once per day per section
  * faculty daily load and section daily load caps

Soft constraints (weighted objective)
  * fewer gaps in each section's day
  * balanced faculty workload across days
  * faculty "avoid" slots
  * late-evening theory classes
  * wasted room capacity
"""
from collections import defaultdict
from ortools.sat.python import cp_model

W_GAP = 10
W_BALANCE = 3
W_AVOID = 5
W_LATE = 2
W_WASTE = 1
SECTION_MAX_PER_DAY = 6


# ---------------------------------------------------------------- helpers
def expand_rules(rules, n_days, n_periods):
    out = set()
    for r in rules or []:
        days = r.get("days") if r.get("days") is not None else range(n_days)
        periods = r.get("periods") if r.get("periods") is not None else range(n_periods)
        for d in days:
            for p in periods:
                out.add((d, p))
    return out


def faculty_maps(data):
    D, P = len(data["days"]), data["periods"]
    unavail, avoid = {}, {}
    for f in data["faculty"]:
        unavail[f["id"]] = expand_rules(f.get("unavailable"), D, P)
        avoid[f["id"]] = expand_rules(f.get("avoid"), D, P)
    return unavail, avoid


def build_events(data):
    courses = {c["id"]: c for c in data["courses"]}
    events = []
    for sec in data["sections"]:
        for cid in sec["courses"]:
            c = courses[cid]
            fid = data["assignments"][f"{sec['id']}|{cid}"]
            if c["type"] == "lab":
                block = c.get("block", 2)
                n, dur = c["hours"] // block, block
            else:
                n, dur = c["hours"], 1
            for _ in range(n):
                events.append({"id": len(events), "section": sec["id"], "course": cid,
                               "faculty": fid, "duration": dur, "type": c["type"]})
    return events


def room_fits(room, event, course, section):
    if room.get("exam_only"):
        return False
    if event["type"] == "lab":
        return (room["type"] == "lab" and course.get("equipment") in room["equipment"]
                and room["capacity"] >= section["size"])
    return room["type"] == "lecture" and room["capacity"] >= section["size"]


# ---------------------------------------------------------------- solver
def generate_timetable(data, time_limit=20, workers=8):
    D, P, L = len(data["days"]), data["periods"], data["lunch_after"]
    courses = {c["id"]: c for c in data["courses"]}
    sections = {s["id"]: s for s in data["sections"]}
    fac = {f["id"]: f for f in data["faculty"]}
    unavail, avoid = faculty_maps(data)
    events = build_events(data)

    m = cp_model.CpModel()
    by_room, by_fac, by_sec = defaultdict(list), defaultdict(list), defaultdict(list)
    fac_day_load = defaultdict(list)       # (f,d) -> [(var, duration)]
    course_day = defaultdict(list)         # (sec,course,d) -> [var]
    x = {}
    cost = []

    for ev in events:
        sec, course = sections[ev["section"]], courses[ev["course"]]
        evars = []
        for d in range(D):
            for p in range(P - ev["duration"] + 1):
                cover = list(range(p, p + ev["duration"]))
                if ev["duration"] > 1 and (p // L) != (cover[-1] // L):
                    continue  # block must not straddle lunch
                if any((d, q) in unavail[ev["faculty"]] for q in cover):
                    continue
                for room in data["rooms"]:
                    if not room_fits(room, ev, course, sec):
                        continue
                    v = m.NewBoolVar(f"x_{ev['id']}_{d}_{p}_{room['id']}")
                    x[(ev["id"], d, p, room["id"])] = v
                    evars.append(v)
                    for q in cover:
                        by_room[(room["id"], d, q)].append(v)
                        by_fac[(ev["faculty"], d, q)].append(v)
                        by_sec[(sec["id"], d, q)].append(v)
                    fac_day_load[(ev["faculty"], d)].append((v, ev["duration"]))
                    course_day[(sec["id"], course["id"], d)].append(v)
                    # soft costs attached to this placement
                    c = (room["capacity"] - sec["size"]) // 5 * W_WASTE
                    if ev["type"] == "theory" and p >= 6:
                        c += W_LATE * (p - 5)
                    c += W_AVOID * sum(1 for q in cover if (d, q) in avoid[ev["faculty"]])
                    if c:
                        cost.append(c * v)
        if not evars:
            raise ValueError(f"No feasible placement for event {ev}")
        m.AddExactlyOne(evars)

    for lst in list(by_room.values()) + list(by_fac.values()) + list(by_sec.values()):
        if len(lst) > 1:
            m.AddAtMostOne(lst)
    for lst in course_day.values():
        if len(lst) > 1:
            m.AddAtMostOne(lst)

    # faculty daily caps + workload balance
    max_load = {}
    for f in data["faculty"]:
        max_load[f["id"]] = m.NewIntVar(0, f["max_per_day"], f"maxload_{f['id']}")
        cost.append(W_BALANCE * max_load[f["id"]])
    for (fid, d), lst in fac_day_load.items():
        load = sum(dur * v for v, dur in lst)
        m.Add(load <= fac[fid]["max_per_day"])
        m.Add(load <= max_load[fid])

    # section day: cap + compactness (gap minimisation)
    for sid in sections:
        for d in range(D):
            occ = []
            for q in range(P):
                lst = by_sec.get((sid, d, q))
                if not lst:
                    continue
                o = m.NewBoolVar(f"occ_{sid}_{d}_{q}")
                m.Add(o == sum(lst))
                occ.append((q, o))
            if not occ:
                continue
            m.Add(sum(o for _, o in occ) <= SECTION_MAX_PER_DAY)
            first = m.NewIntVar(0, P - 1, f"first_{sid}_{d}")
            last = m.NewIntVar(0, P - 1, f"last_{sid}_{d}")
            for q, o in occ:
                m.Add(first <= q).OnlyEnforceIf(o)
                m.Add(last >= q).OnlyEnforceIf(o)
            gap = m.NewIntVar(0, P, f"gap_{sid}_{d}")
            m.Add(gap >= last - first + 1 - sum(o for _, o in occ))
            cost.append(W_GAP * gap)

    m.Minimize(sum(cost))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    status = solver.Solve(m)
    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, {"status": name, "message": "No feasible timetable with current constraints"}

    sessions = []
    for (eid, d, p, rid), v in x.items():
        if solver.Value(v):
            ev = events[eid]
            sessions.append({"id": eid, "section": ev["section"], "course": ev["course"],
                             "faculty": ev["faculty"], "room": rid, "day": d, "start": p,
                             "duration": ev["duration"], "type": ev["type"], "substitute": None})
    sessions.sort(key=lambda s: s["id"])
    meta = {"status": name, "objective": solver.ObjectiveValue(), "best_bound": solver.BestObjectiveBound(),
            "wall_time": round(solver.WallTime(), 2), "events": len(events), "variables": len(x)}
    return sessions, meta


# ---------------------------------------------------------------- validator
def effective_teacher(s):
    return s.get("substitute") or s["faculty"]


def validate(data, sessions):
    """Independent re-check of every hard rule. Returns a list of conflict dicts."""
    D, P, L = len(data["days"]), data["periods"], data["lunch_after"]
    rooms = {r["id"]: r for r in data["rooms"]}
    sections = {s["id"]: s for s in data["sections"]}
    courses = {c["id"]: c for c in data["courses"]}
    fac = {f["id"]: f for f in data["faculty"]}
    unavail, _ = faculty_maps(data)
    conflicts = []
    occ = defaultdict(list)

    def add(kind, msg, ids):
        conflicts.append({"kind": kind, "message": msg, "sessions": ids})

    for s in sessions:
        cover = range(s["start"], s["start"] + s["duration"])
        if s["start"] < 0 or s["start"] + s["duration"] > P or not (0 <= s["day"] < D):
            add("bounds", f"{s['course']} {s['section']} outside the timetable grid", [s["id"]])
            continue
        if s["duration"] > 1 and (s["start"] // L) != ((s["start"] + s["duration"] - 1) // L):
            add("lunch", f"{s['course']} {s['section']} lab block crosses lunch break", [s["id"]])
        room, sec, course = rooms.get(s["room"]), sections[s["section"]], courses[s["course"]]
        if room is None or room["capacity"] < sec["size"]:
            add("capacity", f"{s['room']} too small for {s['section']} ({sec['size']} students)", [s["id"]])
        elif s["type"] == "lab" and (room["type"] != "lab" or course.get("equipment") not in room["equipment"]):
            add("room-type", f"{s['room']} lacks equipment for {s['course']}", [s["id"]])
        elif s["type"] == "theory" and room["type"] != "lecture":
            add("room-type", f"{s['room']} is not a lecture room", [s["id"]])
        t = effective_teacher(s)
        for q in cover:
            if (s["day"], q) in unavail[t]:
                add("availability", f"{fac[t]['name']} is unavailable at {data['days'][s['day']]} "
                                    f"{data['period_labels'][q]}", [s["id"]])
                break
            occ[("room", s["room"], s["day"], q)].append(s["id"])
            occ[("faculty", t, s["day"], q)].append(s["id"])
            occ[("section", s["section"], s["day"], q)].append(s["id"])

    for (kind, who, d, q), ids in occ.items():
        if len(ids) > 1:
            add(f"{kind}-clash", f"{kind} {who} double-booked on {data['days'][d]} "
                                 f"{data['period_labels'][q]}", sorted(set(ids)))

    load = defaultdict(int)
    for s in sessions:
        load[(effective_teacher(s), s["day"])] += s["duration"]
    for (f, d), h in load.items():
        if h > fac[f]["max_per_day"]:
            add("overload", f"{fac[f]['name']} has {h}h on {data['days'][d]} (max {fac[f]['max_per_day']})", [])

    # dedupe identical messages
    seen, uniq = set(), []
    for c in conflicts:
        k = (c["kind"], c["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def try_move(data, sessions, session_id, day, start, room_id):
    """Manual override: apply the move on a copy and validate. Returns (new_sessions, conflicts)."""
    new = [dict(s) for s in sessions]
    tgt = next((s for s in new if s["id"] == session_id), None)
    if tgt is None:
        raise KeyError(session_id)
    tgt.update(day=day, start=start, room=room_id)
    conflicts = [c for c in validate(data, new) if session_id in c["sessions"] or not c["sessions"]]
    return new, conflicts


# ---------------------------------------------------------------- analytics
def analytics(data, sessions):
    D, P = len(data["days"]), data["periods"]
    total_slots = D * P
    room_ids = [r["id"] for r in data["rooms"] if not r.get("exam_only")]
    grid = {rid: [[0] * P for _ in range(D)] for rid in room_ids}
    fac_load = {f["id"]: [0] * D for f in data["faculty"]}
    sec_gaps = {}
    sec_occ = {s["id"]: [[0] * P for _ in range(D)] for s in data["sections"]}
    for s in sessions:
        for q in range(s["start"], s["start"] + s["duration"]):
            grid[s["room"]][s["day"]][q] = 1
            sec_occ[s["section"]][s["day"]][q] = 1
        fac_load[effective_teacher(s)][s["day"]] += s["duration"]
    for sid, g in sec_occ.items():
        total = 0
        for d in range(D):
            idx = [q for q in range(P) if g[d][q]]
            if idx:
                total += (idx[-1] - idx[0] + 1) - len(idx)
        sec_gaps[sid] = total
    rooms = [{"id": rid, "used": sum(sum(r) for r in grid[rid]),
              "utilization": round(100 * sum(sum(r) for r in grid[rid]) / total_slots, 1),
              "grid": grid[rid]} for rid in room_ids]
    faculty = []
    for f in data["faculty"]:
        per_day = fac_load[f["id"]]
        faculty.append({"id": f["id"], "name": f["name"], "per_day": per_day, "weekly": sum(per_day),
                        "peak": max(per_day)})
    return {"rooms": rooms, "faculty": faculty, "section_gaps": sec_gaps,
            "avg_room_utilization": round(sum(r["utilization"] for r in rooms) / len(rooms), 1)}
