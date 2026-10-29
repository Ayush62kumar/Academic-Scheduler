"""Exam scheduling: CP-SAT slot assignment, hall allocation, invigilator duty roster.

Hard: students of a section never sit two exams in one slot; total candidates in a slot
      never exceed total hall capacity.
Soft: avoid two exams on the same day (heavy) or consecutive days (light) for any section.
"""
import math
from datetime import date, timedelta
from itertools import combinations
from collections import defaultdict
from ortools.sat.python import cp_model


def exam_days(cfg):
    d, out = date.fromisoformat(cfg["start_date"]), []
    while len(out) < cfg["num_days"]:
        if d.weekday() != 6:  # skip Sundays
            out.append(d)
        d += timedelta(days=1)
    return out


def generate_exams(data, time_limit=10):
    cfg = data["exam"]
    days = exam_days(cfg)
    slots = [(di, si) for di in range(len(days)) for si in range(len(cfg["slots"]))]
    courses = {c["id"]: c for c in data["courses"] if c["type"] == "theory"}
    taken = defaultdict(set)      # course -> sections
    for s in data["sections"]:
        for cid in s["courses"]:
            if cid in courses:
                taken[cid].add(s["id"])
    size = {cid: sum(next(s["size"] for s in data["sections"] if s["id"] == sid) for sid in secs)
            for cid, secs in taken.items()}
    halls = sorted([r for r in data["rooms"] if r["type"] in ("lecture", "hall")], key=lambda r: -r["capacity"])
    total_cap = sum(h["capacity"] for h in halls)

    m = cp_model.CpModel()
    y = {(c, s): m.NewBoolVar(f"y_{c}_{s}") for c in taken for s in range(len(slots))}
    for c in taken:
        m.AddExactlyOne(y[(c, s)] for s in range(len(slots)))
    for s in range(len(slots)):
        m.Add(sum(size[c] * y[(c, s)] for c in taken) <= total_cap)

    cost = []
    nslot = len(cfg["slots"])
    day_var = {(c, d): m.NewBoolVar(f"d_{c}_{d}") for c in taken for d in range(len(days))}
    for c in taken:
        for d in range(len(days)):
            m.Add(day_var[(c, d)] == sum(y[(c, d * nslot + k)] for k in range(nslot)))
    for a, b in combinations(sorted(taken), 2):
        if not (taken[a] & taken[b]):
            continue
        for s in range(len(slots)):
            m.Add(y[(a, s)] + y[(b, s)] <= 1)
        for d in range(len(days)):
            v = m.NewBoolVar(f"same_{a}_{b}_{d}")
            m.Add(day_var[(a, d)] + day_var[(b, d)] <= 1 + v)
            cost.append(10 * v)
            if d + 1 < len(days):
                for p, q in ((a, b), (b, a)):
                    w = m.NewBoolVar(f"cons_{p}_{q}_{d}")
                    m.Add(day_var[(p, d)] + day_var[(q, d + 1)] <= 1 + w)
                    cost.append(w)
    m.Minimize(sum(cost))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 8
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, {"status": solver.StatusName(status)}

    placement = {c: next(s for s in range(len(slots)) if solver.Value(y[(c, s)])) for c in taken}

    # hall allocation (mixed seating: a hall can host several courses in one slot)
    by_slot = defaultdict(list)
    for c, s in placement.items():
        by_slot[s].append(c)
    per = cfg["students_per_invigilator"]
    duty = defaultdict(int)
    schedule = []
    for s in sorted(by_slot):
        di, si = slots[s]
        left = {h["id"]: h["capacity"] for h in halls}
        hall_courses = defaultdict(dict)
        for c in sorted(by_slot[s], key=lambda c: -size[c]):
            need = size[c]
            for hid in sorted(left, key=lambda h: -left[h]):
                if need == 0:
                    break
                take = min(need, left[hid])
                if take:
                    hall_courses[hid][c] = take
                    left[hid] -= take
                    need -= take
        slot_courses = set(by_slot[s])
        teaching = {data["assignments"][f"{sid}|{c}"] for c in slot_courses for sid in taken[c]}
        pool = [f["id"] for f in data["faculty"] if f["id"] not in teaching]
        used_here = set()
        halls_out = []
        for hid, cs in hall_courses.items():
            count = sum(cs.values())
            need_inv = max(1, math.ceil(count / per))
            chosen = []
            for fid in sorted([p for p in pool if p not in used_here], key=lambda f: duty[f]):
                if len(chosen) == need_inv:
                    break
                chosen.append(fid)
                used_here.add(fid)
                duty[fid] += 1
            halls_out.append({"hall": hid, "candidates": count, "courses": cs, "invigilators": chosen,
                              "invigilators_short": need_inv - len(chosen)})
        schedule.append({
            "date": days[di].isoformat(), "weekday": days[di].strftime("%a"),
            "slot": cfg["slots"][si]["id"], "time": cfg["slots"][si]["label"],
            "exams": [{"course": c, "name": courses[c]["name"], "students": size[c],
                       "sections": sorted(taken[c])} for c in sorted(by_slot[s])],
            "halls": halls_out,
        })

    # verification: no section has two exams in a slot
    clashes = []
    for s, cs in by_slot.items():
        for a, b in combinations(cs, 2):
            if taken[a] & taken[b]:
                clashes.append(f"{a} & {b} share students and the same slot")
    short = sum(h["invigilators_short"] for e in schedule for h in e["halls"])
    meta = {"status": solver.StatusName(status), "objective": solver.ObjectiveValue(),
            "exams": len(taken), "clashes": clashes, "invigilator_shortfall": short,
            "invigilator_duties": dict(duty)}
    return schedule, meta
