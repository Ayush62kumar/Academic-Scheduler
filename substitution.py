"""Faculty substitution: find affected sessions and rank replacement candidates."""
from collections import defaultdict
from solver import faculty_maps, effective_teacher


def _teaching_map(sessions):
    busy = defaultdict(int)   # (faculty, day, period) -> count
    load_day = defaultdict(int)
    load_week = defaultdict(int)
    for s in sessions:
        t = effective_teacher(s)
        for q in range(s["start"], s["start"] + s["duration"]):
            busy[(t, s["day"], q)] += 1
        load_day[(t, s["day"])] += s["duration"]
        load_week[t] += s["duration"]
    return busy, load_day, load_week


def affected_sessions(sessions, faculty_id, day, periods=None):
    out = []
    for s in sessions:
        if s["day"] != day or effective_teacher(s) != faculty_id:
            continue
        cover = set(range(s["start"], s["start"] + s["duration"]))
        if periods is None or cover & set(periods):
            out.append(s)
    return out


def suggest(data, sessions, sub_log, faculty_id, day, periods=None, top_n=5):
    courses = {c["id"]: c for c in data["courses"]}
    fac = {f["id"]: f for f in data["faculty"]}
    unavail, _ = faculty_maps(data)
    busy, load_day, load_week = _teaching_map(sessions)
    done = defaultdict(int)
    for entry in sub_log:
        if not entry.get("reverted"):
            done[entry["substitute"]] += 1

    results = []
    for s in affected_sessions(sessions, faculty_id, day, periods):
        course = courses[s["course"]]
        cover = list(range(s["start"], s["start"] + s["duration"]))
        cands = []
        for f in data["faculty"]:
            if f["id"] == faculty_id:
                continue
            if any(busy.get((f["id"], day, q)) for q in cover):
                continue
            if any((day, q) in unavail[f["id"]] for q in cover):
                continue
            if load_day[(f["id"], day)] + s["duration"] > f["max_per_day"]:
                continue
            reasons, score = [], 0
            if course["subject"] in f["skills"]:
                score += 50
                reasons.append(f"teaches {course['subject']}")
            if f["dept"] == course["dept"]:
                score += 20
                reasons.append(f"same dept ({f['dept']})")
            score -= load_week[f["id"]] * 2
            score -= load_day[(f["id"], day)] * 3
            score -= done[f["id"]] * 5
            reasons.append(f"{load_week[f['id']]}h/week, {load_day[(f['id'], day)]}h that day")
            if done[f["id"]]:
                reasons.append(f"{done[f['id']]} earlier substitution(s)")
            cands.append({"faculty_id": f["id"], "name": f["name"], "score": score, "reasons": reasons,
                          "skill_match": course["subject"] in f["skills"]})
        cands.sort(key=lambda c: -c["score"])
        results.append({"session": s, "course_name": course["name"], "candidates": cands[:top_n]})
    return results


def apply(sessions, sub_log, notifications, data, session_id, substitute_id, reason="Faculty absent"):
    fac = {f["id"]: f for f in data["faculty"]}
    s = next(x for x in sessions if x["id"] == session_id)
    original = s["faculty"]
    s["substitute"] = substitute_id
    entry = {"session_id": session_id, "original": original, "substitute": substitute_id, "reason": reason,
             "course": s["course"], "section": s["section"], "day": s["day"], "start": s["start"],
             "room": s["room"], "reverted": False}
    sub_log.append(entry)
    when = f"{data['days'][s['day']]} {data['period_labels'][s['start']]}"
    notifications.extend([
        {"to_type": "faculty", "to_id": substitute_id, "to": fac[substitute_id]["name"],
         "message": f"You are assigned to cover {s['course']} for {s['section']} on {when} in {s['room']}."},
        {"to_type": "section", "to_id": s["section"], "to": f"Students of {s['section']}",
         "message": f"{s['course']} on {when} will be taken by {fac[substitute_id]['name']} instead of {fac[original]['name']}."},
        {"to_type": "faculty", "to_id": original, "to": fac[original]["name"],
         "message": f"Your {s['course']} session on {when} has been covered by {fac[substitute_id]['name']}."},
    ])
    return entry


def revert(sessions, sub_log, session_id):
    s = next(x for x in sessions if x["id"] == session_id)
    s["substitute"] = None
    for e in sub_log:
        if e["session_id"] == session_id and not e["reverted"]:
            e["reverted"] = True
