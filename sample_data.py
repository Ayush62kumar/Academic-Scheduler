"""Sample institution data for the prototype.

Everything the scheduler needs lives in one plain dict so it can later be
replaced by rows loaded from PostgreSQL / CSV uploads without touching the solver.
"""

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
PERIOD_LABELS = [
    "09:00-10:00", "10:00-11:00", "11:00-12:00", "12:00-13:00",
    "14:00-15:00", "15:00-16:00", "16:00-17:00", "17:00-18:00",
]
LUNCH_AFTER = 4  # lunch break sits between period index 3 and 4


def build_sample_data():
    rooms = [
        {"id": "LH-101", "type": "lecture", "capacity": 60, "equipment": []},
        {"id": "LH-102", "type": "lecture", "capacity": 60, "equipment": []},
        {"id": "LH-201", "type": "lecture", "capacity": 48, "equipment": []},
        {"id": "LH-202", "type": "lecture", "capacity": 45, "equipment": []},
        {"id": "LAB-CS1", "type": "lab", "capacity": 60, "equipment": ["cs"]},
        {"id": "LAB-CS2", "type": "lab", "capacity": 60, "equipment": ["cs"]},
        {"id": "LAB-EC1", "type": "lab", "capacity": 50, "equipment": ["ec"]},
        {"id": "LAB-PH1", "type": "lab", "capacity": 50, "equipment": ["phy"]},
        # exam-only hall (not used for regular teaching)
        {"id": "SEM-HALL", "type": "hall", "capacity": 120, "equipment": [], "exam_only": True},
    ]

    # unavailable / avoid rules: days=None or periods=None means "all"
    faculty = [
        {"id": "F01", "name": "Dr. Anita Sharma", "dept": "CSE", "skills": ["DS", "DBMS"], "max_per_day": 5},
        {"id": "F02", "name": "Prof. Rajesh Kumar", "dept": "CSE", "skills": ["DS", "OS"], "max_per_day": 5,
         "avoid": [{"days": None, "periods": [0]}]},
        {"id": "F03", "name": "Dr. Meera Iyer", "dept": "CSE", "skills": ["DBMS", "OS"], "max_per_day": 5},
        {"id": "F04", "name": "Dr. Sanjay Verma", "dept": "MATH", "skills": ["MATH"], "max_per_day": 5,
         "unavailable": [{"days": [4], "periods": [4, 5, 6, 7]}]},
        {"id": "F05", "name": "Dr. Priya Nair", "dept": "ECE", "skills": ["DIGITAL", "SIGNALS", "ELECTRONICS"],
         "max_per_day": 5, "unavailable": [{"days": [0], "periods": [0]}]},
        {"id": "F06", "name": "Prof. Arun Singh", "dept": "ECE", "skills": ["ANALOG", "DIGITAL"], "max_per_day": 5},
        {"id": "F07", "name": "Dr. Kavita Rao", "dept": "ECE", "skills": ["SIGNALS", "ANALOG"], "max_per_day": 5},
        {"id": "F08", "name": "Ms. Neha Gupta", "dept": "HUM", "skills": ["COMM"], "max_per_day": 4,
         "unavailable": [{"days": [2], "periods": None}]},
        {"id": "F09", "name": "Dr. Vikram Das", "dept": "MATH", "skills": ["MATH"], "max_per_day": 5},
        {"id": "F10", "name": "Mr. Rohit Mishra", "dept": "CSE", "skills": ["DS", "DBMS"], "max_per_day": 4},
        {"id": "F11", "name": "Dr. Sunita Patel", "dept": "PHY", "skills": ["PHYSICS"], "max_per_day": 4,
         "unavailable": [{"days": [0, 4], "periods": None}]},
        {"id": "F12", "name": "Mr. Imran Khan", "dept": "ECE", "skills": ["ELECTRONICS", "DIGITAL"], "max_per_day": 4},
    ]

    courses = [
        {"id": "CS201", "name": "Data Structures", "dept": "CSE", "subject": "DS", "type": "theory", "hours": 3},
        {"id": "CS202", "name": "Database Systems", "dept": "CSE", "subject": "DBMS", "type": "theory", "hours": 3},
        {"id": "CS203", "name": "Operating Systems", "dept": "CSE", "subject": "OS", "type": "theory", "hours": 3},
        {"id": "MA201", "name": "Discrete Mathematics", "dept": "MATH", "subject": "MATH", "type": "theory", "hours": 3},
        {"id": "EC201", "name": "Digital Electronics", "dept": "ECE", "subject": "DIGITAL", "type": "theory", "hours": 3},
        {"id": "EC202", "name": "Signals & Systems", "dept": "ECE", "subject": "SIGNALS", "type": "theory", "hours": 3},
        {"id": "EC203", "name": "Analog Circuits", "dept": "ECE", "subject": "ANALOG", "type": "theory", "hours": 3},
        {"id": "MA202", "name": "Engineering Mathematics", "dept": "MATH", "subject": "MATH", "type": "theory", "hours": 3},
        {"id": "HS201", "name": "Professional Communication", "dept": "HUM", "subject": "COMM", "type": "theory", "hours": 2},
        {"id": "CS291", "name": "Data Structures Lab", "dept": "CSE", "subject": "DS", "type": "lab", "hours": 2,
         "block": 2, "equipment": "cs"},
        {"id": "CS292", "name": "DBMS Lab", "dept": "CSE", "subject": "DBMS", "type": "lab", "hours": 2,
         "block": 2, "equipment": "cs"},
        {"id": "EC291", "name": "Electronics Lab", "dept": "ECE", "subject": "ELECTRONICS", "type": "lab", "hours": 2,
         "block": 2, "equipment": "ec"},
        {"id": "PH291", "name": "Physics Lab", "dept": "PHY", "subject": "PHYSICS", "type": "lab", "hours": 2,
         "block": 2, "equipment": "phy"},
    ]

    cse = ["CS201", "CS202", "CS203", "MA201", "HS201", "CS291", "CS292"]
    ece = ["EC201", "EC202", "EC203", "MA202", "HS201", "EC291", "PH291"]
    sections = [
        {"id": "CSE-A", "dept": "CSE", "size": 55, "courses": cse},
        {"id": "CSE-B", "dept": "CSE", "size": 50, "courses": cse},
        {"id": "ECE-A", "dept": "ECE", "size": 45, "courses": ece},
        {"id": "ECE-B", "dept": "ECE", "size": 40, "courses": ece},
    ]

    assignments = {
        # CSE-A
        "CSE-A|CS201": "F01", "CSE-A|CS202": "F03", "CSE-A|CS203": "F02", "CSE-A|MA201": "F04",
        "CSE-A|HS201": "F08", "CSE-A|CS291": "F10", "CSE-A|CS292": "F10",
        # CSE-B
        "CSE-B|CS201": "F02", "CSE-B|CS202": "F01", "CSE-B|CS203": "F03", "CSE-B|MA201": "F09",
        "CSE-B|HS201": "F08", "CSE-B|CS291": "F10", "CSE-B|CS292": "F10",
        # ECE-A
        "ECE-A|EC201": "F05", "ECE-A|EC202": "F07", "ECE-A|EC203": "F06", "ECE-A|MA202": "F04",
        "ECE-A|HS201": "F08", "ECE-A|EC291": "F12", "ECE-A|PH291": "F11",
        # ECE-B
        "ECE-B|EC201": "F06", "ECE-B|EC202": "F05", "ECE-B|EC203": "F07", "ECE-B|MA202": "F09",
        "ECE-B|HS201": "F08", "ECE-B|EC291": "F12", "ECE-B|PH291": "F11",
    }

    exam = {
        "start_date": "2026-12-07",          # a Monday
        "num_days": 6,                       # Mon-Sat style, Sundays skipped
        "slots": [
            {"id": "FN", "label": "10:00-13:00"},
            {"id": "AN", "label": "14:00-17:00"},
        ],
        "students_per_invigilator": 30,
    }

    return {
        "days": DAYS,
        "periods": len(PERIOD_LABELS),
        "period_labels": PERIOD_LABELS,
        "lunch_after": LUNCH_AFTER,
        "rooms": rooms,
        "faculty": faculty,
        "courses": courses,
        "sections": sections,
        "assignments": assignments,
        "exam": exam,
    }
