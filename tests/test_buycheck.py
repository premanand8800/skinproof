import pytest
from fastapi.testclient import TestClient

from conftest import jpeg
from skinproof.guard import Guard
from skinproof.store import Store
from skinproof.web import create_app


class FakeClaims:
    def __init__(self):
        self.calls = 0

    def extract_claims(self, text=None, url=None, image=None, **kw):
        self.calls += 1
        return {"product": "Test Serum", "source": "text", "cost": 0.005, "model": "m", "claims": [
            {"concern": "pore", "direction": "improve", "timeframe_weeks": 4, "quote": "Smaller pores in 4 weeks"},
            {"concern": "radiance", "direction": "improve", "timeframe_weeks": None, "quote": "Glow"},
            {"concern": None, "direction": "improve", "timeframe_weeks": None, "quote": "Dermatologist tested"}]}


def make(tmp_path, **gk):
    st = Store(tmp_path / "p")
    fc = FakeClaims()
    g = Guard(st.root / "guard.json", **{"daily_units": 1e6, "ip_units": 1e6, "ip_agent_runs": 100, "daily_usd": 100, **gk})
    return TestClient(create_app(object(), st, guard=g, agent_deps={"claims": fc})), fc, g


def test_page_served(tmp_path):
    tc, _, _ = make(tmp_path)
    r = tc.get("/buy")
    assert r.status_code == 200 and "Before you buy" in r.text and "/static/logo.svg" in r.text


def test_buy_check_audit_and_cache(tmp_path):
    tc, fc, _ = make(tmp_path)
    r = tc.post("/api/buy-check", data={"text": "Smaller pores in 4 weeks. Glow. Dermatologist tested."})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["audit"] == {"measurable": 2, "marketing_only": 1, "total": 3, "earliest_week": 4, "earliest_is_stated": True}
    c = {x["quote"]: x for x in j["claims"]}
    assert c["Smaller pores in 4 weeks"]["measurable"] and "By week 4" in c["Smaller pores in 4 weeks"]["plan"]
    assert not c["Dermatologist tested"]["measurable"]
    assert any("pubmed" in x["url"] for x in j["trial"]["refs"])
    assert j["cached"] is False
    r2 = tc.post("/api/buy-check", data={"text": "  smaller pores in 4 weeks. glow. dermatologist TESTED. "}).json()
    assert r2["cached"] is True and r2["cost"] == 0.0 and fc.calls == 1


def test_guard_ip_limit_and_cap(tmp_path):
    tc, fc, _ = make(tmp_path, ip_llm_calls=2)
    for i in range(2):
        assert tc.post("/api/buy-check", data={"text": f"claim {i}"}).status_code == 200
    r = tc.post("/api/buy-check", data={"text": "claim 3"})
    assert r.status_code == 429 and r.json()["kind"] == "ip_llm" and fc.calls == 2
    # another IP is unaffected; cached text still free for the limited IP
    assert tc.post("/api/buy-check", data={"text": "claim 9"}, headers={"cf-connecting-ip": "9.9.9.9"}).status_code == 200
    assert tc.post("/api/buy-check", data={"text": "claim 0"}).json()["cached"] is True
    tc2, fc2, _ = make(tmp_path / "b", daily_usd=0.01)
    assert tc2.post("/api/buy-check", data={"text": "x"}).status_code == 429 and fc2.calls == 0


def test_inputs_validated(tmp_path):
    tc, fc, _ = make(tmp_path)
    assert tc.post("/api/buy-check", data={}).status_code == 422
    assert tc.post("/api/buy-check", data={"url": "http://127.0.0.1:8100/"}).status_code == 422
    assert tc.post("/api/buy-check", data={"url": "file:///etc/passwd"}).status_code == 422
    assert tc.post("/api/buy-check", files={"label": ("a.txt", b"nope", "text/plain")}).status_code == 415
    assert tc.post("/api/buy-check", files={"label": ("a.jpg", jpeg(), "image/jpeg")}).status_code == 200
    assert fc.calls == 1


def test_example_free(tmp_path):
    tc, fc, _ = make(tmp_path)
    j = tc.get("/api/buy-check/example").json()
    assert j["example"] and j["cost"] == 0 and j["audit"]["measurable"] >= 1 and fc.calls == 0
