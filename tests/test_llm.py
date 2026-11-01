import httpx
import pytest

from skinproof import llm


def _ok(**kw):
    body = {"model": "google/gemini-3.8-flash",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.0012}}
    body.update(kw)
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_SLEEP_SCALE", 0)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_chat_success_and_cost():
    seen = {}

    def h(req):
        import json
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return _ok()
    r = llm.chat([{"role": "user", "content": "x"}], client=_client(h))
    assert r["message"]["content"] == "hi" and r["cost"] == 0.0012
    assert seen["body"]["model"] == llm.DEFAULT_MODEL and seen["body"]["max_tokens"] == 4000
    assert seen["auth"] == "Bearer test-key"
    assert "tools" not in seen["body"]


def test_tools_and_format_passed():
    seen = {}

    def h(req):
        import json
        seen["body"] = json.loads(req.content)
        return _ok()
    llm.chat([], tools=[{"type": "function"}], response_format={"type": "json_object"}, client=_client(h))
    assert seen["body"]["tools"] and seen["body"]["response_format"]


def test_retry_then_success():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 2 else _ok()
    assert llm.chat([], client=_client(h))["message"]["content"] == "hi"
    assert n["c"] == 2


def test_fallback_model_on_5xx():
    models = []

    def h(req):
        import json
        m = json.loads(req.content)["model"]
        models.append(m)
        return httpx.Response(500) if m == llm.DEFAULT_MODEL else _ok(model=m)
    r = llm.chat([], client=_client(h))
    assert r["model"] == llm.FALLBACK_MODEL
    assert models[-1] == llm.FALLBACK_MODEL


def test_fallback_on_timeout():
    def h(req):
        import json
        if json.loads(req.content)["model"] == llm.DEFAULT_MODEL:
            raise httpx.ReadTimeout("t")
        return _ok(model=llm.FALLBACK_MODEL)
    assert llm.chat([], client=_client(h))["model"] == llm.FALLBACK_MODEL


def test_4xx_no_retry_no_fallback():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(401, text="nope")
    with pytest.raises(llm.LLMError):
        llm.chat([], client=_client(h))
    assert n["c"] == 1


def test_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(llm, "load_api_key", lambda: None)
    with pytest.raises(llm.LLMError):
        llm.chat([])


def test_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    (tmp_path / ".env").write_text("A=1\nOPENROUTER_API_KEY='abc'\n")
    monkeypatch.chdir(tmp_path)
    assert llm.load_api_key() == "abc"


def test_image_part():
    p = llm.image_part(b"\xff\xd8")
    assert p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go:\n```\n[1, 2]\n```\nbye', [1, 2]),
    ('Sure! {"a": {"b": 2}} hope it helps', {"a": {"b": 2}}),
])
def test_parse_json(text, expected):
    assert llm.parse_json(text) == expected


@pytest.mark.parametrize("bad", [None, "", "no json here", "{broken"])
def test_parse_json_bad(bad):
    with pytest.raises(ValueError):
        llm.parse_json(bad)
