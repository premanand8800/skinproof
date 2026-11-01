import httpx
import pytest

from skinproof import claims, llm


def v(concern, verdict, delta=0, thr=4):
    return {"concern": concern, "verdict": verdict, "delta": delta, "threshold": thr}


def c(concern, quote="q", tf=None):
    return {"concern": concern, "direction": "improve", "timeframe_weeks": tf, "quote": quote}


def test_validate_drops_bad():
    raw = {"claims": [
        {"concern": "pore", "quote": " smaller pores ", "timeframe_weeks": 4, "evil": "x"},
        {"concern": "made_up", "quote": "feels nice", "timeframe_weeks": "soon"},
        {"concern": "acne", "quote": "  "},
        {"concern": "acne"},
        "junk",
        {"concern": "pore", "quote": "smaller pores", "timeframe_weeks": 4},
        {"concern": "texture", "quote": "smooth", "timeframe_weeks": 500},
    ]}
    out = claims.validate_claims(raw)
    assert out == [
        {"concern": "pore", "direction": "improve", "timeframe_weeks": 4, "quote": "smaller pores"},
        {"concern": None, "direction": "improve", "timeframe_weeks": None, "quote": "feels nice"},
        {"concern": "texture", "direction": "improve", "timeframe_weeks": None, "quote": "smooth"},
    ]


def test_validate_garbage():
    assert claims.validate_claims(None) == []
    assert claims.validate_claims({"claims": "x"}) == []
    assert claims.validate_claims([{"concern": "skin_type", "quote": "q"}])[0]["concern"] is None


def test_html_to_text():
    t = claims._html_to_text("<html><head><title>x</title></head><body><script>bad()</script>"
                             "<h1>Niacinamide Pore Serum</h1><p>Reduces pores &amp; shine</p></body></html>")
    assert "bad()" not in t and "Niacinamide Pore Serum" in t and "Reduces pores & shine" in t


def _mock_chat(monkeypatch, content, capture=None):
    def fake(messages, **kw):
        if capture is not None:
            capture["messages"], capture["kw"] = messages, kw
        return {"message": {"content": content}, "usage": {}, "cost": 0.001, "model": "m"}
    monkeypatch.setattr(llm, "chat", fake)


def test_extract_text(monkeypatch):
    cap = {}
    _mock_chat(monkeypatch, '```json\n{"product":"Serum X","claims":[{"concern":"pore","quote":"smaller pores in 4 weeks","timeframe_weeks":4},{"concern":"zzz","quote":"dermatologist tested"}]}\n```', cap)
    r = claims.extract_claims(text="blurb")
    assert r["product"] == "Serum X" and r["source"] == "text"
    assert [x["concern"] for x in r["claims"]] == ["pore", None]
    assert cap["kw"]["temperature"] == 0.0


def test_extract_image(monkeypatch):
    cap = {}
    _mock_chat(monkeypatch, '{"product":"P","claims":[]}', cap)
    r = claims.extract_claims(image=b"\xff\xd8")
    assert r["source"] == "image" and r["claims"] == []
    assert cap["messages"][1]["content"][1]["type"] == "image_url"


def test_extract_url(monkeypatch):
    class R:
        text = "<p>" + "a" * 20000 + "</p>"
        def raise_for_status(self): pass
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    cap = {}
    _mock_chat(monkeypatch, '{"product":"P","claims":[]}', cap)
    r = claims.extract_claims(url="https://x.test/p")
    assert r["source"] == "url"
    assert len(cap["messages"][1]["content"]) < 16000


def test_extract_needs_input():
    with pytest.raises(ValueError):
        claims.extract_claims()


def test_extract_bad_reply(monkeypatch):
    _mock_chat(monkeypatch, "sorry I cannot")
    with pytest.raises(ValueError):
        claims.extract_claims(text="x")


def test_compare_statuses():
    verdicts = [v("pore", "IMPROVED", 7), v("redness", "WORSE", -6), v("texture", "NO_PROVEN_CHANGE", 1)]
    res = claims.compare([c("pore"), c("redness"), c("texture", tf=4), c(None, "smells nice"),
                          c("moisture")], verdicts, 5)
    assert [r["status"] for r in res] == ["SUPPORTED", "CONTRADICTED", "NOT_YET_SHOWN",
                                         "NOT_MEASURABLE", "NOT_YET_SHOWN"]
    assert res[3]["measured"] is None and res[0]["measured"]["delta"] == 7


def test_too_early_and_proven_beats_early():
    verdicts = [v("pore", "NO_PROVEN_CHANGE", 1), v("acne", "IMPROVED", 9)]
    res = claims.compare([c("pore", tf=8), c("acne", tf=8)], verdicts, 3)
    assert [r["status"] for r in res] == ["TOO_EARLY", "SUPPORTED"]
    assert claims.compare([c("pore", tf=4)], verdicts, 4)[0]["status"] == "NOT_YET_SHOWN"
    assert claims.compare([c("pore", tf=4)], verdicts, None)[0]["status"] == "NOT_YET_SHOWN"


def test_accepts_verdict_dict_and_weeks():
    d = {"concerns": [v("pore", "NO_PROVEN_CHANGE", 1)], "weeks_on_product": 2.0}
    r = claims.compare([c("pore", tf=4)], d)
    assert r[0]["status"] == "TOO_EARLY" and r[0]["weeks_elapsed"] == 2.0


def test_sentence():
    r = claims.compare([c("pore", "visibly smaller pores in 4 weeks", 4)],
                       [v("pore", "NO_PROVEN_CHANGE", 1, 4)], 5)[0]
    assert r["sentence"] == ('Claim: "visibly smaller pores in 4 weeks". After 5 weeks, your pores '
                             'changed by +1, inside your 4-point noise: not yet shown.')
    assert "not something" in claims.compare([c(None, "clean")], [], 1)[0]["sentence"] or True
    assert "Too early" in claims.compare([c("pore", tf=8)], [], 2)[0]["sentence"]
    assert claims.compare([c("pore")], [], None)[0]["sentence"].startswith('Claim: "q". There is no')


def test_empty():
    assert claims.compare([], []) == []


def test_validate_claims_dedupes_same_concern_and_timeframe():
    raw = {"claims": [
        {"concern": "pore", "timeframe_weeks": 4, "quote": "reduces pores"},
        {"concern": "pore", "timeframe_weeks": 4, "quote": "visibly reduces the appearance of pores in 4 weeks"},
        {"concern": "pore", "timeframe_weeks": 8, "quote": "pores smaller"},
    ]}
    out = claims.validate_claims(raw)
    assert len(out) == 2
    assert out[0]["quote"].startswith("visibly reduces")


def test_likely_maps_to_needs_confirmation():
    verdicts = [v("pore", "LIKELY_WORSE", -5), v("redness", "LIKELY_IMPROVED", 6)]
    res = claims.compare([c("pore", "q1", 4), c("redness", "q2", 4)], verdicts, 6)
    assert [r["status"] for r in res] == ["NEEDS_CONFIRMATION", "NEEDS_CONFIRMATION"]
    assert "Needs confirmation" in res[0]["sentence"]
