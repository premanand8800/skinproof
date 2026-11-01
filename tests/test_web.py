import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeYouCam, jpeg, task_json
from skinproof.store import Store
from skinproof.web import create_app
from skinproof.youcam import YouCamClient

SCORES = [{"pore": 60, "redness": 55}, {"pore": 63, "redness": 58}, {"pore": 61, "redness": 52}, {"pore": 62, "redness": 56}]


def make(tasks, tmp_path):
    f = FakeYouCam(tasks)
    c = YouCamClient("k", cache_dir=tmp_path / "c", poll_interval=0,
                     http=httpx.Client(transport=httpx.MockTransport(f)))
    return TestClient(create_app(c, Store(tmp_path / "p"))), f


def test_status_and_demo(tmp_path):
    tc, _ = make([task_json()], tmp_path)
    assert tc.get("/api/status").json()["units"] == 988
    d = tc.get("/api/demo").json()
    assert d["sample_data"] and d["ready"] and "overall" not in d
    verdicts = {c["concern"]: c["verdict"] for c in d["concerns"]}
    assert verdicts["pore"] == "IMPROVED" and verdicts["redness"] == "NO_PROVEN_CHANGE"
    assert tc.get("/").status_code == 200


def test_full_journey(tmp_path):
    # distinct images so the cache does not collapse them
    tc, f = make([task_json(s) for s in SCORES], tmp_path)
    pid = tc.post("/api/profiles", json={"name": "T"}).json()["id"]
    files = [("files", (f"{i}.jpg", jpeg((100 + i * 20, 120, 130)), "image/jpeg")) for i in range(4)]
    # each upload hits the same fake task id; tasks cycle in order
    r = tc.post(f"/api/profiles/{pid}/calibrate", files=files)
    assert r.status_code == 200, r.text
    assert r.json()["calibration"]["concerns"]["pore"]["threshold"] >= 3
    tc.post(f"/api/profiles/{pid}/products", json={"name": "Serum", "start_date": "2026-01-01", "concerns": ["pore"]})
    assert tc.get(f"/api/profiles/{pid}/results").json()["ready"] is False
    f.tasks = [task_json({"pore": 50, "redness": 50}), task_json({"pore": 70, "redness": 51}), task_json({"pore": 72, "redness": 50})]
    for i, d in enumerate(["2026-01-02", "2026-02-20", "2026-03-06"]):
        r = tc.post(f"/api/profiles/{pid}/checkins", data={"when": d},
                    files={"file": ("a.jpg", jpeg((10 * i + 5, 50, 90)), "image/jpeg")})
        assert r.json()["counted"]
    res = tc.get(f"/api/profiles/{pid}/results").json()
    by = {c["concern"]: c["verdict"] for c in res["concerns"]}
    assert by == {"pore": "IMPROVED", "redness": "NO_PROVEN_CHANGE"}


def test_bad_photo_not_counted(tmp_path):
    tc, _ = make([task_json(status="error", error="error_src_face_too_small")], tmp_path)
    pid = tc.post("/api/profiles", json={"name": "T"}).json()["id"]
    r = tc.post(f"/api/profiles/{pid}/checkins", files={"file": ("a.jpg", jpeg(), "image/jpeg")}).json()
    assert r["counted"] is False and "60%" in r["checkin"]["error"]["message"]


def test_calibrate_needs_four(tmp_path):
    tc, _ = make([task_json()], tmp_path)
    pid = tc.post("/api/profiles", json={"name": "T"}).json()["id"]
    r = tc.post(f"/api/profiles/{pid}/calibrate", files=[("files", ("a.jpg", jpeg(), "image/jpeg"))] * 3)
    assert r.status_code == 400 and "4 to 6" in r.json()["detail"]


def test_file_traversal_blocked(tmp_path):
    tc, _ = make([task_json()], tmp_path)
    assert tc.get("/api/files/..%2f..%2fpyproject.toml").status_code == 404
