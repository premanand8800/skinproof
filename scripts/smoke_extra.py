"""Live smoke test for youcam_extra. Spends at most 60 units. Never prints the key.

Run: uv run python scripts/smoke_extra.py
Outputs: private/synthetic_face.jpg, private/smoke/*.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skinproof import youcam_extra as X  # noqa: E402
from skinproof.youcam import YouCamClient, YouCamError  # noqa: E402

BUDGET = 60.0
PROMPT = ("Photorealistic studio portrait of a fictional adult woman, around 30 years old, "
          "front-facing, looking straight at the camera, neutral relaxed expression, eyes open, "
          "hair pulled back off the face, forehead and ears visible, no makeup, no glasses, "
          "natural visible skin texture, soft even diffused lighting, plain grey background, "
          "face fills the frame, head and shoulders")
NEG = "makeup, glasses, hat, hair covering face, shadows, side view, cartoon, text, watermark"

priv = ROOT / "private"
(smoke := priv / "smoke").mkdir(parents=True, exist_ok=True)
client = YouCamClient(cache_dir=ROOT / ".cache" / "youcam")
start = client.balance()
print("balance before:", start)
if start is None:
    sys.exit("could not read balance")


def spent() -> float:
    return start - (client.balance() or 0)


def guard(est: float) -> None:
    s = spent()
    if s + est > BUDGET:
        raise SystemExit(f"budget guard: spent {s} + est {est} > {BUDGET}")


def dump(name: str, obj) -> None:
    (smoke / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str))


def step(name, fn, est):
    guard(est)
    try:
        r = fn()
        print(f"{name}: ok units={r.get('units') if isinstance(r, dict) else '?'} spent_total={spent()}")
        return r
    except YouCamError as e:
        print(f"{name}: FAILED {e.code} {e}")
        dump(name + "_error", {"code": e.code, "msg": str(e)})


face_path = priv / "synthetic_face.jpg"
if not face_path.is_file():
    guard(1)
    r = X.text_to_image(client, PROMPT, negative_prompt=NEG, size="1328*1328", with_meta=True)
    face_path.write_bytes(r["image"])
    dump("text_to_image", r["raw"])
    print("t2i units", r["units"], "spent", spent())
face = face_path.read_bytes()

r = step("photo_lighting", lambda: X.photo_lighting(client, face), 2)
if r:
    dump("photo_lighting", r["raw"]); (priv / "smoke" / "lit.jpg").write_bytes(r["image"])
r = step("skin_simulation", lambda: X.skin_simulation(client, face, {"redness": 0.5, "pores": 0.5, "texture": 0.3}), 4)
if r:
    dump("skin_simulation", r["raw"]); (priv / "smoke" / "sim.jpg").write_bytes(r["image"])
r = step("fitzpatrick", lambda: X.fitzpatrick(client, face), 10)
if r:
    dump("fitzpatrick", r["raw"]); print("  type:", r["type"])
r = step("face_attributes", lambda: X.face_attributes(client, face), 10)
if r:
    dump("face_attributes", r["raw"]); print("  ", {k: r[k] for k in ("yaw", "pitch", "roll", "face_ratio", "face_quality")})
r = step("skin_tone", lambda: X.skin_tone(client, face), 20)
if r:
    dump("skin_tone", r["raw"]); print("  tone:", r["tone"])

guard(9)
try:
    a = client.analyze(face, ["wrinkle", "pore", "texture", "redness"])
    dump("skin_analysis", a["raw"])
    print("skin analysis ok scores:", {k: v["raw_score"] for k, v in a["scores"].items()})
except YouCamError as e:
    print("skin analysis FAILED", e.code, e)

end = client.balance()
print("balance after:", end, "spent:", start - end)
