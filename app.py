"""FastAPI backend.

Only the admin logs in. The admin builds and edits the timetable (a private draft) and then
presses "Publish". The published copy is a frozen snapshot that everyone (teachers, students,
visitors) can view without logging in. Edits and substitutions stay invisible to the public
until the admin publishes again.

Run:  python -m uvicorn app:app
"""
import copy
import hmac
import hashlib
import json
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import exams as exam_mod
import substitution as sub
from sample_data import build_sample_data
from solver import analytics, effective_teacher, generate_timetable, try_move, validate

app = FastAPI(title="Academic Scheduler Prototype")
BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

STORE = Path(os.environ.get("SCHEDULER_STORE", BASE)) 
STORE.mkdir(parents=True, exist_ok=True)   # where data.json / accounts.json / activity.json live
DATA_FILE, ACCOUNTS_FILE, PUBLISHED_FILE = STORE / "data.json", STORE / "accounts.json", STORE / "published.json"
LOCK = threading.RLock()
SESSION_TTL = 12 * 3600
MAX_FAILS, LOCK_SECONDS = 5, 60


# ------------------------------------------------------------------ persistence
def _save(path, obj):
    content = json.dumps(obj, indent=1)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content)
        for _ in range(5):
            try:
                os.replace(tmp, path)
                return
            except (PermissionError, OSError):
                time.sleep(0.05)
        path.write_text(content)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    except Exception:
        path.write_text(content)


def _load_data():
    return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else build_sample_data()


STATE = {"data": _load_data(), "sessions": None, "meta": {}, "exams": None, "exam_meta": {}, "sub_log": []}
PUBLISHED = json.loads(PUBLISHED_FILE.read_text()) if PUBLISHED_FILE.exists() else None


# ------------------------------------------------------------------ accounts and sessions
def _hash(pw, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000).hex()


def _new_user(username, role, password):
    salt = secrets.token_hex(16)
    return {"username": username, "role": role, "faculty_id": None, "salt": salt, "hash": _hash(password, salt),
            "must_change": False, "temp_password": None}


def _set_password(u, pw, temporary):
    u["salt"] = secrets.token_hex(16)
    u["hash"] = _hash(pw, u["salt"])
    u["must_change"] = temporary
    u["temp_password"] = pw if temporary else None


USERS: Dict[str, dict] = json.loads(ACCOUNTS_FILE.read_text()) if ACCOUNTS_FILE.exists() else {}
SESSIONS: Dict[str, dict] = {}
FAILS: Dict[str, list] = {}          # username -> [count, first_failure_ts]


def ensure_accounts():
    """Only the admin account exists. Any old teacher accounts are removed."""
    with LOCK:
        for name in [k for k, u in USERS.items() if u["role"] != "admin"]:
            del USERS[name]
        if "admin" not in USERS:
            USERS["admin"] = _new_user("admin", "admin", os.environ.get("ADMIN_PASSWORD", "admin123"))
        USERS["admin"]["must_change"] = False
        USERS["admin"]["temp_password"] = None
        for tok in [t for t, x in SESSIONS.items() if x["username"] not in USERS]:
            del SESSIONS[tok]
        _save(ACCOUNTS_FILE, USERS)


def _me(u):
    name = "Administrator"
    return {"username": u["username"], "role": u["role"], "faculty_id": u["faculty_id"], "name": name,
            "must_change": u["must_change"]}


def session_user(request: Request):
    tok = request.cookies.get("session")
    s = SESSIONS.get(tok) if tok else None
    if not s or s["exp"] < time.time() or s["username"] not in USERS:
        SESSIONS.pop(tok, None)
        raise HTTPException(401, "Please log in")
    return USERS[s["username"]]


def current_user(u=Depends(session_user)):
    if u["must_change"]:
        raise HTTPException(403, "You must change your password first")
    return u


def admin_only(u=Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return u


class LoginReq(BaseModel):
    username: str
    password: str


class PasswordReq(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/login")
def login(req: LoginReq, response: Response):
    key = req.username.strip().lower()
    fails = FAILS.get(key)
    if fails and fails[0] >= MAX_FAILS and time.time() - fails[1] < LOCK_SECONDS:
        raise HTTPException(429, f"Too many failed attempts. Try again in {LOCK_SECONDS} seconds.")
    u = USERS.get(key)
    good = hmac.compare_digest(_hash(req.password, u["salt"] if u else "00" * 16), u["hash"] if u else "x")
    if not (u and good):
        f = FAILS.get(key)
        if not f or time.time() - f[1] >= LOCK_SECONDS:
            f = FAILS[key] = [0, time.time()]
        f[0] += 1
        raise HTTPException(401, "Invalid username or password")
    FAILS.pop(key, None)
    tok = secrets.token_urlsafe(32)
    SESSIONS[tok] = {"username": key, "exp": time.time() + SESSION_TTL}
    response.set_cookie("session", tok, httponly=True, samesite="lax", max_age=SESSION_TTL)
    return _me(u)


@app.post("/api/logout")
def logout(request: Request, response: Response):
    SESSIONS.pop(request.cookies.get("session"), None)
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(session_user)):
    return _me(u)


@app.post("/api/change-password")
def change_password(req: PasswordReq, request: Request, u=Depends(session_user)):
    if not hmac.compare_digest(_hash(req.old_password, u["salt"]), u["hash"]):
        raise HTTPException(400, "Current password is wrong")
    if len(req.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    if req.new_password == req.old_password:
        raise HTTPException(400, "New password must be different from the current one")
    with LOCK:
        _set_password(u, req.new_password, temporary=False)
        mine = request.cookies.get("session")
        for tok in [t for t, s in SESSIONS.items() if s["username"] == u["username"] and t != mine]:
            del SESSIONS[tok]
        _save(ACCOUNTS_FILE, USERS)
    return _me(u)


# ------------------------------------------------------------------ timetable
def _ensure_timetable():
    if STATE["sessions"] is None:
        sessions, meta = generate_timetable(STATE["data"], time_limit=15)
        if sessions is None:
            raise HTTPException(422, meta)
        STATE.update(sessions=sessions, meta=meta, sub_log=[])


@app.on_event("startup")
def _startup():
    ensure_accounts()
    _ensure_timetable()


class GenerateReq(BaseModel):
    time_limit: int = 15


class MoveReq(BaseModel):
    session_id: int
    day: int
    start: int
    room: str


class SuggestReq(BaseModel):
    faculty_id: str
    day: int
    periods: Optional[List[int]] = None


class ApplyReq(BaseModel):
    session_id: int
    substitute_id: str
    reason: str = "Faculty absent"


class RevertReq(BaseModel):
    session_id: int


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
def chrome_devtools():
    return Response(status_code=204)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/data")
def get_data(_=Depends(current_user)):
    return STATE["data"]


def _timetable_payload():
    return {"sessions": STATE["sessions"], "meta": STATE["meta"],
            "conflicts": validate(STATE["data"], STATE["sessions"])}


@app.get("/api/timetable")
def get_timetable(_=Depends(current_user)):
    _ensure_timetable()
    return _timetable_payload()


@app.post("/api/timetable/generate")
def regenerate(req: GenerateReq, _=Depends(admin_only)):
    with LOCK:
        sessions, meta = generate_timetable(STATE["data"], time_limit=max(2, min(req.time_limit, 120)))
        if sessions is None:
            raise HTTPException(422, meta)
        STATE.update(sessions=sessions, meta=meta, sub_log=[])
        return _timetable_payload()


@app.post("/api/timetable/move")
def move(req: MoveReq, _=Depends(admin_only)):
    with LOCK:
        _ensure_timetable()
        old = next((s for s in STATE["sessions"] if s["id"] == req.session_id), None)
        if old is None:
            raise HTTPException(404, "Unknown session")
        before = (old["day"], old["start"], old["room"])
        new, conflicts = try_move(STATE["data"], STATE["sessions"], req.session_id, req.day, req.start, req.room)
        if conflicts:
            return {"ok": False, "conflicts": conflicts}
        STATE["sessions"] = new
        d = STATE["data"]
        return {"ok": True, **_timetable_payload()}


@app.get("/api/analytics")
def get_analytics(_=Depends(admin_only)):
    _ensure_timetable()
    return analytics(STATE["data"], STATE["sessions"])


# ------------------------------------------------------------------ substitutions (admin)
@app.post("/api/substitution/suggest")
def suggest(req: SuggestReq, _=Depends(admin_only)):
    _ensure_timetable()
    return sub.suggest(STATE["data"], STATE["sessions"], STATE["sub_log"], req.faculty_id, req.day, req.periods)


@app.post("/api/substitution/apply")
def apply_sub(req: ApplyReq, _=Depends(admin_only)):
    with LOCK:
        _ensure_timetable()
        out = []
        entry = sub.apply(STATE["sessions"], STATE["sub_log"], out, STATE["data"], req.session_id,
                          req.substitute_id, req.reason)
        return {"entry": entry, **_timetable_payload()}


@app.post("/api/substitution/revert")
def revert_sub(req: RevertReq, _=Depends(admin_only)):
    with LOCK:
        _ensure_timetable()
        sub.revert(STATE["sessions"], STATE["sub_log"], req.session_id)
        return _timetable_payload()


@app.get("/api/substitution/log")
def sub_log(_=Depends(admin_only)):
    return {"log": STATE["sub_log"], "notifications": []}


# ------------------------------------------------------------------ exams
@app.post("/api/exams/generate")
def gen_exams(_=Depends(admin_only)):
    with LOCK:
        schedule, meta = exam_mod.generate_exams(STATE["data"])
        if schedule is None:
            raise HTTPException(422, meta)
        STATE.update(exams=schedule, exam_meta=meta)
        return {"schedule": schedule, "meta": meta}


@app.get("/api/exams")
def get_exams(_=Depends(current_user)):
    return {"schedule": STATE["exams"], "meta": STATE["exam_meta"]}


# ------------------------------------------------------------------ master data (admin)
class FacultyIn(BaseModel):
    name: str
    dept: str
    skills: List[str] = []
    max_per_day: int = 5
    unavailable_days: List[int] = []
    avoid_first_period: bool = False


class RoomIn(BaseModel):
    id: str
    type: str
    capacity: int
    equipment: List[str] = []


class SectionIn(BaseModel):
    id: str
    dept: str
    size: int
    assignments: Dict[str, str]   # course id -> faculty id


def _changed():
    STATE["exams"] = None
    _save(DATA_FILE, STATE["data"])


def _resolve(snapshot):
    """Re-solve after a structural change; restore the previous data if it cannot be scheduled."""
    try:
        sessions, meta = generate_timetable(STATE["data"], time_limit=15)
    except ValueError as e:
        STATE["data"] = snapshot
        raise HTTPException(422, f"Cannot schedule this change: {e}")
    if sessions is None:
        STATE["data"] = snapshot
        raise HTTPException(422, "No feasible timetable with this change (too many hours for the available "
                                 "faculty, rooms or time slots). Nothing was saved.")
    STATE.update(sessions=sessions, meta=meta, sub_log=[])


@app.post("/api/faculty")
def add_faculty(f: FacultyIn, _=Depends(admin_only)):
    with LOCK:
        d = STATE["data"]
        if not f.name.strip() or not f.dept.strip():
            raise HTTPException(400, "Name and department are required")
        if not 1 <= f.max_per_day <= 8:
            raise HTTPException(400, "Max hours per day must be between 1 and 8")
        nums = [int(x["id"][1:]) for x in d["faculty"] if x["id"][1:].isdigit()]
        rec = {"id": f"F{(max(nums) if nums else 0) + 1:02d}", "name": f.name.strip(), "dept": f.dept.strip().upper(),
               "skills": [s.strip().upper() for s in f.skills if s.strip()], "max_per_day": f.max_per_day}
        if f.unavailable_days:
            rec["unavailable"] = [{"days": f.unavailable_days, "periods": None}]
        if f.avoid_first_period:
            rec["avoid"] = [{"days": None, "periods": [0]}]
        d["faculty"].append(rec)
        _changed()
        return rec


@app.delete("/api/faculty/{fid}")
def delete_faculty(fid: str, _=Depends(admin_only)):
    with LOCK:
        d = STATE["data"]
        used = sorted({k.split("|")[0] for k, v in d["assignments"].items() if v == fid})
        if used:
            raise HTTPException(409, f"{fid} teaches in {', '.join(used)}. Reassign or delete those sections first.")
        d["faculty"] = [f for f in d["faculty"] if f["id"] != fid]
        _changed()
        return {"ok": True}


@app.post("/api/rooms")
def add_room(r: RoomIn, _=Depends(admin_only)):
    with LOCK:
        d = STATE["data"]
        rid = r.id.strip().upper()
        if not rid:
            raise HTTPException(400, "Room ID is required")
        if any(x["id"] == rid for x in d["rooms"]):
            raise HTTPException(409, f"Room {rid} already exists")
        if r.type not in ("lecture", "lab", "hall") or r.capacity < 1:
            raise HTTPException(400, "Type must be lecture, lab or hall, and capacity at least 1")
        rec = {"id": rid, "type": r.type, "capacity": r.capacity,
               "equipment": [e.strip().lower() for e in r.equipment if e.strip()]}
        if r.type == "hall":
            rec["exam_only"] = True
        d["rooms"].append(rec)
        _changed()
        return rec


@app.delete("/api/rooms/{rid}")
def delete_room(rid: str, _=Depends(admin_only)):
    with LOCK:
        snap = copy.deepcopy(STATE["data"])
        STATE["data"]["rooms"] = [r for r in snap["rooms"] if r["id"] != rid]
        if any(s["room"] == rid for s in (STATE["sessions"] or [])):
            _resolve(snap)           # room was in use: re-solve without it (rolls back if impossible)
        _changed()
        return {"ok": True}


@app.post("/api/sections")
def add_section(s: SectionIn, _=Depends(admin_only)):
    with LOCK:
        d = STATE["data"]
        sid = s.id.strip().upper()
        if not sid or not s.dept.strip() or s.size < 1:
            raise HTTPException(400, "Section ID, department and a size of at least 1 are required")
        if any(x["id"] == sid for x in d["sections"]):
            raise HTTPException(409, f"Section {sid} already exists")
        if not s.assignments:
            raise HTTPException(400, "Select at least one course with a teacher")
        courses = {c["id"]: c for c in d["courses"]}
        fac = {f["id"] for f in d["faculty"]}
        for cid, fid in s.assignments.items():
            if cid not in courses or fid not in fac:
                raise HTTPException(400, f"Unknown course or teacher: {cid} / {fid}")
            c = courses[cid]
            if c["type"] == "lab":
                ok = any(r["type"] == "lab" and c.get("equipment") in r["equipment"] and r["capacity"] >= s.size
                         for r in d["rooms"])
                if not ok:
                    raise HTTPException(400, f"No lab with '{c.get('equipment')}' equipment fits {s.size} students for {cid}")
            elif not any(r["type"] == "lecture" and r["capacity"] >= s.size for r in d["rooms"]):
                raise HTTPException(400, f"No lecture room holds {s.size} students. Add a bigger room first.")
        snap = copy.deepcopy(d)
        d["sections"].append({"id": sid, "dept": s.dept.strip().upper(), "size": s.size, "courses": list(s.assignments)})
        for cid, fid in s.assignments.items():
            d["assignments"][f"{sid}|{cid}"] = fid
        _resolve(snap)
        _changed()
        return {"ok": True}


@app.delete("/api/sections/{sid}")
def delete_section(sid: str, _=Depends(admin_only)):
    with LOCK:
        d = STATE["data"]
        d["sections"] = [x for x in d["sections"] if x["id"] != sid]
        d["assignments"] = {k: v for k, v in d["assignments"].items() if k.split("|")[0] != sid}
        if STATE["sessions"]:
            STATE["sessions"] = [x for x in STATE["sessions"] if x["section"] != sid]
        _changed()
        return {"ok": True}


@app.post("/api/reset")
def reset_data(_=Depends(admin_only)):
    with LOCK:
        if DATA_FILE.exists():
            DATA_FILE.unlink()
        STATE["data"] = build_sample_data()
        _resolve(STATE["data"])
        STATE["exams"] = None
        return {"ok": True}


# ------------------------------------------------------------------ publishing
def _public_view(snap):
    """What visitors may see: a trimmed, read-only copy. No accounts, constraints or internal notes."""
    d = snap["data"]
    keep = ("id", "section", "course", "faculty", "substitute", "room", "day", "start", "duration", "type")
    return {
        "published": True,
        "published_at": snap["published_at"],
        "data": {
            "days": d["days"], "periods": d["periods"], "period_labels": d["period_labels"],
            "lunch_after": d["lunch_after"],
            "faculty": [{"id": f["id"], "name": f["name"], "dept": f["dept"]} for f in d["faculty"]],
            "courses": [{"id": c["id"], "name": c["name"], "type": c["type"], "hours": c["hours"]} for c in d["courses"]],
            "sections": [{"id": x["id"], "dept": x["dept"], "courses": x["courses"]} for x in d["sections"]],
            "rooms": [{"id": r["id"], "type": r["type"], "capacity": r["capacity"],
                       **({"exam_only": True} if r.get("exam_only") else {})} for r in d["rooms"]],
        },
        "sessions": [{k: s.get(k) for k in keep} for s in snap["sessions"]],
        "exams": snap.get("exams"),
    }


def _draft_snapshot():
    return {"data": copy.deepcopy(STATE["data"]), "sessions": copy.deepcopy(STATE["sessions"]),
            "exams": copy.deepcopy(STATE["exams"])}


def _unpublished_changes():
    if PUBLISHED is None:
        return True
    cur = _draft_snapshot()
    return any(json.dumps(cur[k], sort_keys=True) != json.dumps(PUBLISHED.get(k), sort_keys=True)
               for k in ("data", "sessions", "exams"))


@app.get("/api/public/timetable")
def public_timetable():
    """No login needed. Always returns the last published version, never the draft."""
    if PUBLISHED is None:
        return {"published": False}
    return _public_view(PUBLISHED)


@app.get("/api/publish/status")
def publish_status(_=Depends(admin_only)):
    return {"published": PUBLISHED is not None, "published_at": PUBLISHED and PUBLISHED["published_at"],
            "unpublished_changes": _unpublished_changes()}


@app.post("/api/publish")
def publish(_=Depends(admin_only)):
    global PUBLISHED
    with LOCK:
        _ensure_timetable()
        conflicts = validate(STATE["data"], STATE["sessions"])
        if conflicts:
            raise HTTPException(409, f"Fix the {len(conflicts)} conflict(s) before publishing.")
        PUBLISHED = {**_draft_snapshot(), "published_at": datetime.now().isoformat(timespec="seconds")}
        _save(PUBLISHED_FILE, PUBLISHED)
        return {"published": True, "published_at": PUBLISHED["published_at"], "unpublished_changes": False}


@app.post("/api/unpublish")
def unpublish(_=Depends(admin_only)):
    global PUBLISHED
    with LOCK:
        PUBLISHED = None
        if PUBLISHED_FILE.exists():
            PUBLISHED_FILE.unlink()
        return {"published": False, "unpublished_changes": True}
