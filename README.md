# Academic Scheduler – Prototype

Centralized, constraint-based scheduling for timetables, room/lab allocation,
faculty substitutions and examinations.

## Run

```bash
pip install -r requirements.txt
uvicorn app:app --reload          # then open http://127.0.0.1:8000
python test_prototype.py          # end-to-end checks (no server needed)
```

The first start solves a timetable (about 6 s for the sample data).

## Who can do what

* **Admin** logs in (top-right *Admin login*), builds and edits the timetable as a private draft, then presses
  **Publish timetable**. The header shows *Published* or *Unpublished changes*.
* **Everyone else** (teachers, students, visitors) opens the home page and sees the last *published* timetable
  (by section, teacher or room) and the exam schedule. No login, no accounts, read-only.
* Edits, regenerations and substitutions stay invisible to the public until the admin publishes again.
* The published copy is saved in `published.json`. It survives restarts.

## What it does

| Module | File | How |
|---|---|---|
| Timetable generation | `solver.py` | OR-Tools CP-SAT. One boolean per (session, day, period, room). |
| Conflict validation | `solver.py` | Independent re-check of every hard rule; used after every manual edit or substitution. |
| Room & lab allocation | `solver.py` | Room type, capacity and lab equipment are filtered before the model is built. |
| Substitutions | `substitution.py` | Finds the absent teacher's sessions, ranks free candidates by skill match, department, workload and fairness. Notifications are simulated. |
| Exams | `exams.py` | CP-SAT slot assignment (no student clash, hall capacity), hall allocation with mixed seating, balanced invigilator roster. |
| Analytics | `solver.py` | Room utilization, faculty load per day, section idle gaps. |
| API | `app.py` | FastAPI, state held in memory. |
| UI | `static/index.html` | Timetable by section / faculty / room, move a session, heatmap, substitution flow, exams, master data. |

## Constraints

Hard: no faculty / room / section double-booking; room type, capacity and equipment fit;
faculty availability; contact hours met; lab blocks (2 h) stay on one side of lunch;
theory course at most once a day per section; faculty and section daily caps.

Soft (weights at the top of `solver.py`): idle gaps in a section's day, balanced faculty load,
faculty "avoid" slots, late-evening theory, wasted room capacity.

## Managing teachers, rooms and sections from the UI

Open the **Master data** tab:

* **Add teacher** – name, department, skills, max hours/day, unavailable days, "avoid first period".
* **Add room / lab** – ID, type (lecture / lab / exam hall), capacity, lab equipment tag. Regenerate the timetable to use it.
* **Add section** – ID, department, size, then tick the courses and pick a teacher for each. The timetable is re-solved
  automatically. If the change cannot be scheduled (for example one teacher is given too many hours), it is rejected and
  nothing is saved.
* Delete buttons: a teacher who still teaches a section is blocked; deleting a room in use re-solves without it;
  deleting a section drops its sessions.
* Changes are saved to `data.json` next to `app.py` and reloaded on restart. **Reset to sample data** (or deleting
  `data.json`) restores the original.

## Using your own data

`sample_data.py` returns one dict (rooms, faculty, courses, sections, assignments, exam config).
Replace `build_sample_data()` with a loader for CSV or PostgreSQL and nothing else changes.

## Prototype limits / next steps

* State is in memory (restarting resets substitutions and manual edits) – move to PostgreSQL.
* Each section is one student group; electives and split lab batches need group-overlap modeling.
* Single-campus; add travel gaps for multi-campus faculty.
* Notifications are logged, not sent (plug in email / SMS / push).
* Single admin account (default `admin` / `admin123`; set `ADMIN_PASSWORD` before first start and change it from the Password button).
* Large institutions: solve per department or add a time budget with warm-start from the last timetable.