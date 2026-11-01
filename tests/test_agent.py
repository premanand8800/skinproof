import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import jpeg
from skinproof import noise
from skinproof.agent import MAX_STEPS, Agent, decide
from skinproof.budget import BudgetExceeded, UnitBudget
from skinproof.store import Store
from skinproof.web import create_app
from skinproof.youcam import YouCamClient

RETAKES = [{"pore": 61, "redness": 55}, {"pore": 64, "redness": 59}, {"pore": 59, "redness": 53}]


def tc_msg(*calls, content=None):
    return {"message": {"role": "assistant", "content": content, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
        for i, (n, a) in enumerate(calls)]}, "usage": {}, "cost": 0.001, "model": "fake"}


def text_msg(t="hmm"):
    return {"message": {"role": "assistant", "content": t}, "usage": {}, "cost": 0.001, "model": "fake"}


class FakeChat:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def __call__(self, messages, tools=None, **kw):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": [t["function"]["name"] for t in tools or []]})
        return self.script.pop(0) if self.script else text_msg()


class FakeClient:
    def __init__(self, scores=None):
        self.scores, self.n = scores or {"pore": 72, "redness": 56}, 0

    def analyze(self, data, actions=None):
        self.n += 1
        return {"scores": {c: {"raw_score": v, "mask_urls": []} for c, v in self.scores.items()}, "cached": False}


class FakeExtra:
    def __init__(self, yaw_new=2):
        self.yaw_new, self.calls = yaw_new, []

    def estimated_cost(self, f, n=None):
        return {"face_attributes": 3, "skin_tone": 3, "fitzpatrick": 3, "skin_simulation": 8, "skin_analysis": 12}.get(f, 5)

    def face_attributes(self, client, img):
        self.calls.append("fa")
        fq = {"has_face": True, "area": "good", "frontal": "good", "lighting": "good",
              "faceangle": "good" if self.yaw_new < 10 else "bad"}
        return {"face_quality": fq, "face_ratio": 0.6, "units": 3}

    def skin_tone(self, c, img):
        return {"tone": "medium", "units": 3}

    def fitzpatrick(self, c, img):
        return {"type": "III", "units": 3}

    def skin_simulation(self, c, img, targets):
        return {"image": b"SIM", "units": 8}


class FakeClaims:
    def __init__(self, timeframe=8):
        self.tf, self.extracted = timeframe, 0

    def extract_claims(self, text=None, url=None, image=None):
        self.extracted += 1
        return {"product": "Serum", "source": "text", "claims": [
            {"concern": "pore", "direction": "improve", "timeframe_weeks": self.tf, "quote": "minimises pores"}]}

    def compare(self, claims, verdicts, weeks_elapsed=None):
        out = []
        for c in claims:
            v = next((x for x in verdicts if x["concern"] == c["concern"]), None)
            if v is None:
                st = "NOT_MEASURABLE"
            elif v["verdict"] == "IMPROVED":
                st = "SUPPORTED"
            elif v["verdict"] == "WORSE":
                st = "CONTRADICTED"
            elif weeks_elapsed is not None and weeks_elapsed < c["timeframe_weeks"]:
                st = "TOO_EARLY"
            else:
                st = "NOT_YET_SHOWN"
            out.append({"concern": c["concern"], "quote": c["quote"], "timeframe_weeks": c["timeframe_weeks"],
                        "weeks_elapsed": weeks_elapsed, "measured": v, "status": st})
        return out


def setup(tmp_path, latest_pore=60, with_claim=True, start="2026-09-01"):
    store = Store(tmp_path / "p")
    p = store.create_profile("T")
    store.set_calibration(p["id"], noise.build_calibration(real=RETAKES))
    prod = store.add_product(p["id"], "Serum", start, ["pore"])
    if with_claim:
        store._mutate(p["id"], lambda pr: pr["products"][0].update(claim_text="minimises pores in 8 weeks"))
    ph = store.save_photo(p["id"], "checkin", jpeg())
    store.add_checkin(p["id"], {"date": start, "photo": ph, "scores": {"pore": 60, "redness": 55}})
    store.add_checkin(p["id"], {"date": "2026-09-20", "photo": ph, "scores": {"pore": latest_pore, "redness": 55}})
    return store, p["id"], prod["id"]


def make_agent(store, pid, script, photo=None, when="2026-11-01", extra=None, claims=None, budget=None, client=None, **kw):
    chat = FakeChat(script)
    a = Agent(store=store, client=client or FakeClient(), profile_id=pid, photo=photo, when=when, chat=chat,
              extra=extra or FakeExtra(), claims=claims or FakeClaims(), budget=budget, **kw)
    return a, chat


def run(a):
    return list(a.run())


def final(evs):
    return [e for e in evs if e["type"] == "final"][-1]["data"]


FULL = [tc_msg(("get_context", {})), tc_msg(("compare_with_noise", {})), tc_msg(("read_product_claims", {})),
        tc_msg(("check_claims", {}))]


def finish(decision, **kw):
    return tc_msg(("finish", {"decision": decision, "headline": "h", "reasons": ["r"], **kw}))


def test_happy_path_keep(tmp_path):
    store, pid, _ = setup(tmp_path)
    a, chat = make_agent(store, pid, [tc_msg(("get_context", {})), tc_msg(("check_photo_consistency", {})),
                                      tc_msg(("analyze_skin", {})), tc_msg(("skin_context", {})),
                                      tc_msg(("compare_with_noise", {})), tc_msg(("read_product_claims", {})),
                                      tc_msg(("check_claims", {})), tc_msg(("simulate_claimed_result", {"concern": "pore"})),
                                      finish("KEEP")], photo=b"NEW" + jpeg())
    evs = run(a)
    f = final(evs)
    assert f["decision"] == "KEEP" and not f["fallback"] and f["disagreement"] is None
    assert f["simulation_image"].startswith("/api/files/") and "Simulation" in f["simulation_label"]
    assert [e["type"] for e in evs][0] == "start" and evs[-1]["type"] == "final"
    assert {"type", "step", "summary", "units_spent", "units_left_in_budget"} <= set(evs[1])
    assert f["units_spent"] == 12 + 3 + 3 + 3 + 8
    assert f["claims"][0]["status"] == "SUPPORTED"
    assert len(store.get_profile(pid)["checkins"]) == 3
    assert (store.root / "runs" / f"{a.run_id}.json").is_file()


def test_no_photo_hides_photo_tools(tmp_path):
    store, pid, _ = setup(tmp_path)
    a, chat = make_agent(store, pid, [tc_msg(("get_context", {}))] )
    run(a)
    assert "analyze_skin" not in chat.calls[0]["tools"] and "finish" in chat.calls[0]["tools"]


def test_budget_refusal_before_call(tmp_path):
    store, pid, _ = setup(tmp_path)
    client = FakeClient()
    a, chat = make_agent(store, pid, [tc_msg(("analyze_skin", {})), finish("WAIT")], photo=jpeg(),
                         budget=UnitBudget(max_units=10), client=client)
    evs = run(a)
    assert client.n == 0 and a.budget.spent == 0
    res = [e for e in evs if e["type"] == "tool_result"][0]
    assert res["data"]["error"] and "budget" in json.dumps(res["data"]["result"])


def test_malformed_args_and_unknown_tool(tmp_path):
    store, pid, _ = setup(tmp_path)
    a, chat = make_agent(store, pid, [tc_msg(("get_context", "{not json")), tc_msg(("nope", {})),
                                      tc_msg(("simulate_claimed_result", {"bogus": 1}))])
    evs = run(a)
    results = [e for e in evs if e["type"] == "tool_result"]
    assert all(r["data"]["error"] for r in results[:3])
    # errors were fed back to the model
    tool_msgs = [m for m in chat.calls[1]["messages"] if m["role"] == "tool"]
    assert "Malformed" in tool_msgs[0]["content"]


def test_repeated_call_cached(tmp_path):
    store, pid, _ = setup(tmp_path)
    claims = FakeClaims()
    a, _ = make_agent(store, pid, [tc_msg(("read_product_claims", {})), tc_msg(("read_product_claims", {}))], claims=claims)
    evs = run(a)
    assert claims.extracted == 1
    assert "cached" in [e for e in evs if e["type"] == "tool_result"][1]["summary"]


def test_no_tool_call_nudged_then_fallback(tmp_path):
    store, pid, _ = setup(tmp_path)  # 20 days, pore delta 0 -> WAIT
    a, chat = make_agent(store, pid, [text_msg("I think keep")] * MAX_STEPS)
    evs = run(a)
    assert len(chat.calls) == MAX_STEPS
    assert chat.calls[1]["messages"][-1]["role"] == "user"
    f = final(evs)
    assert f["fallback"] is True and f["decision"] == "WAIT" and f["fallback_reason"]
    assert a.step == MAX_STEPS


def test_finish_refused_before_measuring(tmp_path):
    store, pid, _ = setup(tmp_path)
    a, _ = make_agent(store, pid, [finish("KEEP")] + [text_msg()] * 11)
    evs = run(a)
    assert [e for e in evs if e["type"] == "tool_result"][0]["data"]["error"]
    assert final(evs)["fallback"]


def test_disagreement_recorded_not_overridden(tmp_path):
    # 8+ weeks, no proven pore change, claim past timeframe -> rule says STOP; model says KEEP
    store, pid, _ = setup(tmp_path, latest_pore=61)
    store.add_checkin(pid, {"date": "2026-11-01", "scores": {"pore": 61, "redness": 55}})
    a, _ = make_agent(store, pid, FULL + [finish("KEEP")], when="2026-11-01")
    f = final(run(a))
    assert f["decision"] == "KEEP" and f["fallback"] is False
    assert f["disagreement"]["model"]["decision"] == "KEEP" and f["disagreement"]["rule"]["decision"] == "STOP"
    assert f["rule_decision"]["decision"] == "STOP"


def test_retake_blocks_measure(tmp_path):
    store, pid, _ = setup(tmp_path)
    ex = FakeExtra(yaw_new=30)
    client = FakeClient()
    a, _ = make_agent(store, pid, [tc_msg(("check_photo_consistency", {})), tc_msg(("analyze_skin", {})),
                                   tc_msg(("finish", {"decision": "WAIT", "wait_weeks": 0, "headline": "Retake", "reasons": ["yaw"]}))],
                      photo=jpeg(), extra=ex, client=client)
    evs = run(a)
    assert client.n == 0
    f = final(evs)
    assert f["decision"] == "WAIT" and f["wait_weeks"] == 0 and f["disagreement"] is None


def test_llm_budget_triggers_fallback(tmp_path):
    store, pid, _ = setup(tmp_path)
    b = UnitBudget(40, max_llm_usd=0.0015)
    a, chat = make_agent(store, pid, [tc_msg(("get_context", {})), tc_msg(("get_context", {})), text_msg()], budget=b)
    evs = run(a)
    assert any(e["type"] == "error" and "LLM cap" in e["summary"] for e in evs)
    assert final(evs)["fallback"]


def test_llm_exception_fallback(tmp_path):
    store, pid, _ = setup(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("down")
    a = Agent(store=store, client=FakeClient(), profile_id=pid, chat=boom, extra=FakeExtra(), claims=FakeClaims())
    evs = run(a)
    assert evs[-1]["type"] == "final" and evs[-1]["data"]["fallback"]


def test_bad_photo_error_fed_back(tmp_path):
    from skinproof.youcam import YouCamError
    store, pid, _ = setup(tmp_path)

    class Bad(FakeClient):
        def analyze(self, data, actions=None):
            raise YouCamError("error_src_face_too_small")
    a, _ = make_agent(store, pid, [tc_msg(("analyze_skin", {}))], photo=jpeg(), client=Bad())
    evs = run(a)
    r = [e for e in evs if e["type"] == "tool_result"][0]
    assert r["data"]["error"]
    assert store.get_profile(pid)["checkins"][-1].get("error")  # stored but not counted


# ---- decide()

V = lambda c, v, t=True: {"concern": c, "label": c, "verdict": v, "targeted": t}


@pytest.mark.parametrize("verdicts,checks,weeks,exp", [
    ([], [], 3, "WAIT"),
    ([V("pore", "WORSE")], [{"concern": "pore", "status": "CONTRADICTED"}], 3, "STOP"),
    ([V("pore", "IMPROVED")], [{"concern": "pore", "status": "SUPPORTED"}], 3, "KEEP"),
    ([V("pore", "IMPROVED"), V("redness", "WORSE")], [{"concern": "pore", "status": "SUPPORTED"}, {"concern": "redness", "status": "CONTRADICTED"}], 3, "STOP"),
    ([V("pore", "NO_PROVEN_CHANGE")], [{"concern": "pore", "status": "TOO_EARLY", "timeframe_weeks": 8}], 3, "WAIT"),
    ([V("pore", "NO_PROVEN_CHANGE")], [{"concern": "pore", "status": "NOT_YET_SHOWN", "timeframe_weeks": 8}], 9, "STOP"),
    ([V("pore", "NO_PROVEN_CHANGE")], [], 3, "WAIT"),
    ([V("pore", "NO_PROVEN_CHANGE")], [], 9, "STOP"),
    ([V("pore", "IMPROVED")], [], 3, "KEEP"),
    ([V("pore", "NO_PROVEN_CHANGE")], [{"concern": "pore", "status": "NOT_MEASURABLE"}], 3, "WAIT"),
])
def test_decide(verdicts, checks, weeks, exp):
    assert decide(verdicts, checks, weeks)["decision"] == exp


def test_decide_wait_weeks():
    d = decide([V("pore", "NO_PROVEN_CHANGE")], [{"concern": "pore", "status": "TOO_EARLY", "timeframe_weeks": 8}], 3)
    assert d["wait_weeks"] == 5


# ---- web / SSE

def parse_sse(text):
    return [json.loads(l[6:]) for l in text.split("\n\n") if l.startswith("data: ")]


def test_sse_endpoint_and_saved_run(tmp_path):
    store, pid, _ = setup(tmp_path)
    chat = FakeChat(FULL + [finish("WAIT", wait_weeks=2)])
    app = create_app(YouCamClient("k", cache_dir=tmp_path / "c"), store,
                     agent_deps={"chat": chat, "extra": FakeExtra(), "claims": FakeClaims()})
    tc = TestClient(app)
    r = tc.post(f"/api/profiles/{pid}/agent", data={"when": "2026-10-01"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = parse_sse(r.text)
    assert evs[0]["type"] == "start" and evs[-1]["type"] == "final"
    rid = r.headers["x-run-id"]
    saved = tc.get(f"/api/agent/runs/{rid}").json()
    assert saved["run_id"] == rid and len(saved["events"]) == len(evs)
    assert tc.get("/api/agent/runs/zzzz").status_code == 404
    assert tc.get("/api/agent/runs/ffffffffffff").status_code == 404


def test_sse_needs_product_and_valid_date(tmp_path):
    tc = TestClient(create_app(YouCamClient("k", cache_dir=tmp_path / "c"), Store(tmp_path / "p")))
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    assert tc.post(f"/api/profiles/{pid}/agent").status_code == 409
    tc.post(f"/api/profiles/{pid}/products", json={"name": "S"})
    assert tc.post(f"/api/profiles/{pid}/agent", data={"when": "nope"}).status_code == 400
    assert tc.post("/api/profiles/missing/agent").status_code == 404


def test_product_claim_inputs_json_and_multipart(tmp_path):
    tc = TestClient(create_app(YouCamClient("k", cache_dir=tmp_path / "c"), Store(tmp_path / "p")))
    pid = tc.post("/api/profiles", json={"name": "x"}).json()["id"]
    p = tc.post(f"/api/profiles/{pid}/products", json={"name": "S", "claim_text": "fades spots", "claim_url": "https://x.test/p"}).json()
    assert p["claim_text"] == "fades spots"
    p2 = tc.post(f"/api/profiles/{pid}/products", data={"name": "T", "concerns": "pore,redness,bogus", "claim_text": "calms"},
                 files={"label": ("l.jpg", jpeg(), "image/jpeg")}).json()
    assert p2["concerns"] == ["pore", "redness"] and p2["claim_image"].startswith(pid)
    stored = tc.get(f"/api/profiles/{pid}").json()["products"]
    assert stored[0]["claim_url"] and stored[1]["claim_image"]


def test_demo_agent_trace_valid(tmp_path):
    tc = TestClient(create_app(YouCamClient("k", cache_dir=tmp_path / "c"), Store(tmp_path / "p")))
    d = tc.get("/api/demo/agent").json()
    assert d["sample"] is True and d["events"][0]["type"] == "start" and d["events"][-1]["type"] == "final"
    fin = d["events"][-1]["data"]
    assert fin["decision"] in ("KEEP", "STOP", "WAIT") and "overall" not in json.dumps(d)
    spent = [e["units_spent"] for e in d["events"]]
    assert spent == sorted(spent) and spent[-1] <= 40
