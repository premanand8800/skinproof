"""Regenerate demo/agent_trace.json: a scripted replay of the real Agent loop on the SAMPLE data in
demo/profile.json. Offline: fake YouCam, fake LLM, zero units, zero spend. Run: uv run python scripts/make_demo_trace.py"""
import io, json, tempfile
from pathlib import Path

from PIL import Image

from skinproof import claims, noise
from skinproof.agent import Agent
from skinproof.store import Store

ROOT = Path(__file__).resolve().parents[1]
prof = json.loads((ROOT / "demo" / "profile.json").read_text())
COST = {"face_attributes": 10, "skin_analysis": 12, "skin_tone": 20, "fitzpatrick": 10, "skin_simulation": 4}


class Extra:
    def estimated_cost(self, f, n=None):
        return COST.get(f, 8)

    def face_attributes(self, c, img):
        return {"face_quality": {"has_face": True, "area": "good", "frontal": "good", "lighting": "good",
                                 "faceangle": "good"}, "face_ratio": 0.6, "units": 10}

    def skin_simulation(self, c, img, t):
        return {"image": b"SIM", "units": 4}


class Client:
    def analyze(self, data, actions=None):
        s = prof["checkins"][-1]["scores"]
        return {"scores": {k: {"raw_score": v, "mask_urls": []} for k, v in s.items()}, "cached": False}


class Claims:
    def extract_claims(self, **kw):
        return {"product": prof["product"]["name"], "source": "text", "claims": [
            {"concern": "pore", "direction": "improve", "timeframe_weeks": 8, "quote": "Visibly minimises the look of pores in 8 weeks"},
            {"concern": "texture", "direction": "improve", "timeframe_weeks": 4, "quote": "Smoother-looking texture in 4 weeks"},
            {"concern": "redness", "direction": "improve", "timeframe_weeks": 6, "quote": "Calms the look of redness by week 6"}]}

    compare = staticmethod(claims.compare)


def call(i, name, args=None):
    return {"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}}


def say(text, name, args=None):
    m = call(0, name, args)
    m["message"]["content"] = text
    return {**m, "usage": {}, "cost": 0.0008, "model": "sample"}


SCRIPT = [
    say("First I need the history, the noise thresholds and how long the product has been used.", "get_context"),
    say("A new photo came in. Before measuring I check it matches the baseline pose and framing.", "check_photo_consistency"),
    say("Photo is consistent. Measuring skin now (raw scores only).", "analyze_skin"),
    say("Now compare each concern with the noise measured on this face. A change only counts as proven when the two latest check-ins both beat the noise.", "compare_with_noise"),
    say("Read what the product claims so I judge it against its own promises.", "read_product_claims"),
    say("Check each claim against the measurements and the weeks elapsed.", "check_claims"),
    say("Show what the claimed pore improvement would look like. This is a simulation only.", "simulate_claimed_result", {"concern": "pore"}),
    say("Pores and texture were beyond noise on the last two check-ins in a row, so they are proven. Redness did not move. Keep, with that caveat.", "finish", {
        "decision": "KEEP",
        "headline": "Keep it: pores and texture improved beyond your own measurement noise, on two check-ins in a row.",
        "reasons": ["Pores: up 8 and 9 points on the last two check-ins, past the 5-point noise band. Proven.",
                    "Texture: up 6.5 and 7.5 points on the last two check-ins, past the 4-point noise band. Proven.",
                    "Redness: no change beyond noise although the label promises it by week 6; do not rely on it for redness."]}),
]


def main():
    tmp = Path(tempfile.mkdtemp())
    store = Store(tmp)
    p = store.create_profile("demo")
    cal = noise.build_calibration(real=prof["retakes"])
    store.set_calibration(p["id"], cal)
    pr = store.add_product(p["id"], prof["product"]["name"], prof["product"]["start_date"], prof["product"]["concerns"])
    for c in prof["checkins"][:-1]:
        store.add_checkin(p["id"], {"date": c["date"], "scores": c["scores"]})
    buf = io.BytesIO(); Image.new("RGB", (300, 400), (180, 140, 120)).save(buf, "JPEG")
    it = iter(SCRIPT)
    chat = lambda messages, tools=None, **kw: next(it)
    a = Agent(store=store, client=Client(), profile_id=p["id"], product_id=pr["id"], photo=buf.getvalue(),
              when=prof["checkins"][-1]["date"], chat=chat, extra=Extra(), claims=Claims())
    evs = list(a.run())
    fin = evs[-1]["data"]
    assert fin["decision"] == "KEEP" and not fin["fallback"] and fin["disagreement"] is None, fin
    fin["simulation_image"] = None
    for e in evs:
        if e["type"] == "tool_result" and e.get("tool") == "simulate_claimed_result":
            e["data"]["result"]["url"] = None
    fin["run_id"] = "sample"; fin["model"] = "sample (scripted replay)"
    out = {"sample": True, "note": "SAMPLE trace on invented data (demo/profile.json). Scripted replay of the real agent loop, not a live model run; no real face, no units spent.",
           "run_id": "sample", "profile_id": "demo", "model": "sample (scripted replay)", "date": prof["checkins"][-1]["date"],
           "fallback": False, "events": evs}
    (ROOT / "demo" / "agent_trace.json").write_text(json.dumps(out, indent=1, default=str))
    print(fin["decision"], [(v["concern"], v["verdict"], v["recent_deltas"]) for v in fin["verdicts"]], fin["units_spent"])


main()
