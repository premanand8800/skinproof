import io

import httpx
from fastapi.testclient import TestClient

from conftest import FakeYouCam, jpeg, task_json
from skinproof.guard import Guard, GuardLimit
from skinproof.store import Store
from skinproof.web import create_app
from skinproof.youcam import YouCamClient


def make(tmp_path, **gk):
    f = FakeYouCam([task_json({"pore": 60 + i, "redness": 50}) for i in range(8)])
    c = YouCamClient("k", cache_dir=tmp_path / "c", poll_interval=0,
                     http=httpx.Client(transport=httpx.MockTransport(f)))
    store = Store(tmp_path / "p")
    g = Guard(store.root / "guard.json", **gk)
    return TestClient(create_app(c, store, guard=g), raise_server_exceptions=False), store, g, f


def imgs(n, base=0):
    return [("files", (f"{i}.jpg", jpeg((50 + base + i * 15, 100, 130)), "image/jpeg")) for i in range(n)]


def test_guard_global_and_ip(tmp_path):
    g = Guard(tmp_path / "g.json", daily_units=30, ip_units=20, ip_agent_runs=1, daily_usd=1)
    g.reserve_units("a", 12)
    try:
        g.reserve_units("a", 12)
        assert False
    except GuardLimit as e:
        assert e.kind == "ip"
    g.reserve_units("b", 12)
    try:
        g.reserve_units("c", 12)
        assert False
    except GuardLimit as e:
        assert e.kind == "global"
    assert g.units_left() == 6
    g.refund_units("b", 12)
    assert g.units_left() == 18
    # persisted
    assert Guard(tmp_path / "g.json", daily_units=30).units_left() == 18


def test_agent_run_and_usd_caps(tmp_path):
    g = Guard(tmp_path / "g.json", daily_units=500, ip_units=500, ip_agent_runs=2, daily_usd=0.5)
    g.reserve_agent("a", 4)
    g.settle_agent("a", 4, 0, 0.3)
    g.reserve_agent("a", 4)
    g.settle_agent("a", 4, 0, 0.3)
    for ip, kind in (("a", "ip_runs"), ("b", "usd")):
        try:
            g.reserve_agent(ip, 4)
            assert False
        except GuardLimit as e:
            assert e.kind == kind


def test_calibrate_429_before_paid_call(tmp_path):
    tc, store, g, f = make(tmp_path, daily_units=30, ip_units=30)
    pid = tc.post("/api/profiles", json={}).json()["id"] if False else tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    r = tc.post(f"/api/profiles/{pid}/calibrate", files=imgs(4))
    assert r.status_code == 429 and r.json()["error"] == "limit" and "sample" in r.json()["detail"]
    assert f.analyses == 0
    assert tc.get("/api/status").json()["live_units_left"] == 30


def test_ip_header_isolates_and_checkin_cap(tmp_path):
    tc, store, g, f = make(tmp_path, daily_units=1000, ip_units=12)
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    one = lambda: tc.post(f"/api/profiles/{pid}/checkins", headers={"CF-Connecting-IP": "1.1.1.1"},
                          files={"file": ("a.jpg", jpeg((9, 9, 9)), "image/jpeg")})
    assert one().status_code == 200
    assert one().status_code == 429
    r = tc.post(f"/api/profiles/{pid}/checkins", headers={"CF-Connecting-IP": "2.2.2.2"},
                files={"file": ("a.jpg", jpeg((19, 9, 9)), "image/jpeg")})
    assert r.status_code == 200


def test_agent_run_cap(tmp_path):
    tc, store, g, f = make(tmp_path, ip_agent_runs=0)
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    tc.post(f"/api/profiles/{pid}/products", json={"name": "S", "concerns": ["pore"]})
    r = tc.post(f"/api/profiles/{pid}/agent")
    assert r.status_code == 429


def test_profile_ids_unguessable_and_no_listing(tmp_path):
    tc, store, g, f = make(tmp_path)
    ids = {tc.post("/api/profiles", json={"name": "x"}).json()["id"] for _ in range(5)}
    assert len(ids) == 5 and all(len(i) >= 20 for i in ids)
    for path in ("/api/profiles", "/api/profiles/", "/api/files", "/api/files/"):
        assert tc.get(path).status_code in (404, 405)
    assert tc.get("/api/profiles/nope").status_code == 404


def test_files_isolation_and_traversal(tmp_path):
    tc, store, g, f = make(tmp_path)
    a = store.create_profile("a")["id"]
    b = store.create_profile("b")["id"]
    rel = store.save_photo(a, "calib", jpeg())
    assert tc.get(f"/api/files/{rel}").status_code == 200
    (store.root / "secret.txt").write_text("s")
    (store.root / "db.json").exists()
    for bad in ("db.json", "guard.json", f"{a}/../db.json", f"{a}/%2e%2e/db.json", "../db.json",
                f"{b}/{rel.split('/')[1]}", "%2e%2e/%2e%2e/etc/passwd", "/etc/passwd", f"{a}//etc/passwd",
                f"{a}/secret.txt", f"unknown1234/{rel.split('/')[1]}"):
        assert tc.get(f"/api/files/{bad}").status_code == 404, bad
    # symlink inside profile dir pointing out
    link = store.root / a / "calib-deadbeef.jpg"
    link.symlink_to(store.root / "db.json")
    assert tc.get(f"/api/files/{a}/calib-deadbeef.jpg").status_code == 404
    link.unlink()
    # profile dir itself symlinked
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "calib-abcdef12.jpg").write_bytes(jpeg())
    c = store.create_profile("c")["id"]
    (store.root / c).symlink_to(other, target_is_directory=True)
    assert tc.get(f"/api/files/{c}/calib-abcdef12.jpg").status_code == 404


def test_delete_profile_removes_data(tmp_path):
    tc, store, g, f = make(tmp_path)
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    rel = store.save_photo(pid, "calib", jpeg())
    assert (store.root / rel).is_file()
    assert tc.delete(f"/api/profiles/{pid}").json() == {"deleted": True}
    assert not (store.root / pid).exists()
    assert tc.get(f"/api/profiles/{pid}").status_code == 404
    assert tc.get(f"/api/files/{rel}").status_code == 404
    assert tc.delete(f"/api/profiles/{pid}").status_code == 404


def test_upload_limits(tmp_path):
    tc, store, g, f = make(tmp_path)
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    assert tc.post(f"/api/profiles/{pid}/calibrate", files=imgs(7)).status_code == 400
    big = b"\xff\xd8" + b"0" * (10 * 1024 * 1024 + 10)
    r = tc.post(f"/api/profiles/{pid}/checkins", files={"file": ("a.jpg", big, "image/jpeg")})
    assert r.status_code == 413
    r = tc.post(f"/api/profiles/{pid}/checkins", files={"file": ("a.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 415
    r = tc.post(f"/api/profiles/{pid}/checkins", files={"file": ("a.txt", jpeg(), "text/plain")})
    assert r.status_code == 415
    assert f.analyses == 0


def test_generic_500_no_leak(tmp_path):
    tc, store, g, f = make(tmp_path)
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    store.get_profile = lambda p: (_ for _ in ()).throw(RuntimeError("sk-SECRET-KEY /home/x/trace"))
    r = tc.get(f"/api/profiles/{pid}")
    assert r.status_code == 500 and "SECRET" not in r.text and "Traceback" not in r.text
