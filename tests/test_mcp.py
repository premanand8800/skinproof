import json

import httpx
import pytest

from conftest import jpeg, task_json
from skinproof import mcp_server, noise
from skinproof.agent import Agent
from skinproof.store import Store
from skinproof.youcam import YouCamError
from skinproof.youcam_mcp import YouCamMCP

RETAKES = [{"pore": 61, "redness": 55}, {"pore": 64, "redness": 59}, {"pore": 59, "redness": 53}, {"pore": 62, "redness": 57}]


class FakeMCPServer:
    """JSON-RPC handler: one tool call answers as JSON, another as SSE."""
    def __init__(self, scores=None):
        self.calls, self.headers = [], []
        self.scores = scores or {"pore": 60.5, "redness": 55.0}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        msg = json.loads(request.content)
        self.headers.append(dict(request.headers))
        m = msg["method"]
        self.calls.append((m, msg.get("params", {}).get("name")))
        if m == "notifications/initialized":
            return httpx.Response(202)
        if m == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "S1"}, json={
                "jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        if m == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
                "tools": [{"name": "AI-Skin-Analysis", "inputSchema": {}}]}})
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == "upload_file":
            payload = {"result": [{"file_id": "F1"}]}
        elif name == "AI-Skin-Analysis":
            assert args["request"]["src_file_id"] == "F1"
            payload = {"result": task_json(self.scores)}
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="event: message\ndata: " + json.dumps(body) + "\n\n")
        elif name == "Get-Unit-Info":
            payload = {"result": {"results": [{"amount_dec": 600.0}, {"amount_dec": 40.0}]}}
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(payload)}]}})


def client(tmp_path, srv=None):
    srv = srv or FakeMCPServer()
    return YouCamMCP("beauty", "k3y", http=httpx.Client(transport=httpx.MockTransport(srv)),
                     cache_dir=tmp_path / "c", poll_interval=0), srv


def test_handshake_list_and_auth(tmp_path):
    c, srv = client(tmp_path)
    assert [t["name"] for t in c.list_tools()] == ["AI-Skin-Analysis"]
    assert [m for m, _ in srv.calls] == ["initialize", "notifications/initialized", "tools/list"]
    assert srv.headers[0]["authorization"] == "Bearer k3y"
    assert srv.headers[2]["mcp-session-id"] == "S1"
    assert "k3y" not in repr(c)


def test_analyze_via_mcp_matches_rest_shape_and_caches(tmp_path):
    c, srv = client(tmp_path)
    img = jpeg()
    r = c.analyze(img, ["pore", "redness"])
    assert r["scores"]["pore"]["raw_score"] == 60.5 and r["overall"] == 70 and r["cached"] is False
    n = len(srv.calls)
    again = c.analyze(img, ["pore", "redness"])
    assert again["cached"] is True and len(srv.calls) == n
    assert c.balance() == 640.0


def test_rpc_error_and_missing_key(tmp_path):
    c, _ = client(tmp_path)
    with pytest.raises(YouCamError):
        c.call_tool("Unknown")
    c._key = None
    with pytest.raises(YouCamError) as e:
        c.list_tools()
    assert e.value.code == "NoApiKey"


def test_agent_transport_switch(tmp_path, monkeypatch):
    store = Store(tmp_path / "p")
    p = store.create_profile("T")
    store.add_product(p["id"], "Serum", "2026-09-01", ["pore"])
    a = Agent(store=store, client="REST", profile_id=p["id"])
    assert a.transport == "rest" and a._analyzer() == "REST"
    monkeypatch.setenv("SKINPROOF_YOUCAM_TRANSPORT", "mcp")
    fake = object()
    a = Agent(store=store, client="REST", profile_id=p["id"], mcp_client=fake)
    assert a.transport == "mcp" and a._analyzer() is fake
    with pytest.raises(ValueError):
        Agent(store=store, client="REST", profile_id=p["id"], transport="carrier-pigeon")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "p")
    p = s.create_profile("T")
    s.set_calibration(p["id"], noise.build_calibration(real=RETAKES))
    prod = s.add_product(p["id"], "Serum", "2026-09-01", ["pore"])
    s.add_checkin(p["id"], {"date": "2026-09-01", "scores": {"pore": 60, "redness": 55}})
    s.add_checkin(p["id"], {"date": "2026-10-20", "scores": {"pore": 75, "redness": 55}})
    s.add_checkin(p["id"], {"date": "2026-10-27", "scores": {"pore": 76, "redness": 55}})
    s.pid, s.prod = p["id"], prod["id"]
    return s


def test_explain_noise(store):
    r = mcp_server.explain_noise(store.pid, store)
    assert r["concerns"]["pore"]["proof_threshold_points"] >= 3
    assert "error" in mcp_server.explain_noise("nope", store)


def test_should_i_keep_buying_is_free_and_deterministic(store):
    r = mcp_server.should_i_keep_buying(store.pid, store.prod, store)
    assert r["decision"] == "KEEP" and r["paid_calls"] == 0 and r["llm_calls"] == 0
    assert "error" in mcp_server.should_i_keep_buying("nope", None, store)


def test_read_claims_is_opt_in(monkeypatch):
    monkeypatch.delenv("SKINPROOF_MCP_ALLOW_LLM", raising=False)
    monkeypatch.setattr(mcp_server.claims_mod, "extract_claims", lambda **k: pytest.fail("LLM called"))
    assert "disabled" in mcp_server.read_claims(text="x")["error"]
    monkeypatch.setenv("SKINPROOF_MCP_ALLOW_LLM", "1")
    monkeypatch.setattr(mcp_server.claims_mod, "extract_claims", lambda **k: {"claims": [], "product": "P"})
    assert mcp_server.read_claims(text="x")["product"] == "P"


def test_demo_runs():
    runs = mcp_server.demo_runs()
    assert runs and runs[0]["decision"] in ("KEEP", "STOP", "WAIT")


def test_server_registers_four_tools():
    import asyncio
    names = {t.name for t in asyncio.run(mcp_server.build_server().list_tools())}
    assert names == {"skinproof_explain_noise", "skinproof_should_i_keep_buying",
                     "skinproof_read_claims", "skinproof_demo_runs"}
