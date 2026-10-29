"""End-to-end checks. Run:  python test_prototype.py"""
import os, tempfile
os.environ["SCHEDULER_STORE"] = tempfile.mkdtemp()      # keep the test away from real data
from fastapi.testclient import TestClient
from app import app


def main():
    with TestClient(app) as c:
        # nothing is visible before the admin publishes, and no login is needed to ask
        assert c.get("/api/public/timetable").json() == {"published": False}
        # anonymous users cannot reach admin APIs
        for url in ("/api/timetable", "/api/data", "/api/publish/status"):
            assert c.get(url).status_code == 401, url
        # teacher accounts do not exist
        assert c.post("/api/login", json={"username": "f01", "password": "x"}).status_code == 401
        assert c.post("/api/publish").status_code == 401
        print("public sees nothing yet; admin APIs locked")

        r = c.post("/api/login", json={"username": "admin", "password": "admin123"})
        assert r.status_code == 200, f"Login failed: {r.text}"

        tt = c.get("/api/timetable").json()
        assert tt["meta"]["status"] in ("OPTIMAL", "FEASIBLE")
        assert tt["conflicts"] == [], tt["conflicts"]
        print("timetable:", tt["meta"]["status"], len(tt["sessions"]), "sessions, 0 conflicts")
        assert c.get("/api/public/timetable").json()["published"] is False   # draft is not public

        # publish
        assert c.get("/api/publish/status").json()["unpublished_changes"] is True
        assert c.post("/api/publish").json()["published"] is True
        assert c.get("/api/publish/status").json()["unpublished_changes"] is False

        anon = TestClient(app)                       # a visitor with no cookie
        pub = anon.get("/api/public/timetable").json()
        assert pub["published"] and len(pub["sessions"]) == len(tt["sessions"])
        assert "assignments" not in pub["data"] and "unavailable" not in str(pub["data"]["faculty"])
        print("visitor sees the published timetable:", len(pub["sessions"]), "sessions")

        # a draft edit (substitution) must stay private until republished
        fid, day = tt["sessions"][0]["faculty"], tt["sessions"][0]["day"]
        sug = c.post("/api/substitution/suggest", json={"faculty_id": fid, "day": day}).json()
        sid, sb = sug[0]["session"]["id"], sug[0]["candidates"][0]["faculty_id"]
        assert c.post("/api/substitution/apply", json={"session_id": sid, "substitute_id": sb}).json()["conflicts"] == []
        assert c.get("/api/publish/status").json()["unpublished_changes"] is True
        before = next(s for s in anon.get("/api/public/timetable").json()["sessions"] if s["id"] == sid)
        assert before["substitute"] is None
        c.post("/api/publish")
        after = next(s for s in anon.get("/api/public/timetable").json()["sessions"] if s["id"] == sid)
        assert after["substitute"] == sb
        c.post("/api/substitution/revert", json={"session_id": sid})
        print("draft edits stay hidden until the admin publishes again")

        ex = c.post("/api/exams/generate").json()
        assert ex["meta"]["clashes"] == [] and ex["meta"]["invigilator_shortfall"] == 0
        c.post("/api/publish")
        assert anon.get("/api/public/timetable").json()["exams"]
        print("exams published too")

        assert c.post("/api/unpublish").json()["published"] is False
        assert anon.get("/api/public/timetable").json() == {"published": False}
        print("unpublish works")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
