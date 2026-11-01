"""Lighting experiment: raw vs Photo Lighting, per-concern spread. See bench/lighting_README.md.
Costs units: per photo one skin analysis (12) raw + 2 lighting + 12 analysis of the lit image.
Usage: python scripts/lighting_experiment.py PHOTO_DIR [--yes-spend]"""
import statistics
import sys
from pathlib import Path

from skinproof import youcam_extra
from skinproof.youcam import DEFAULT_ACTIONS, YouCamClient

ROOT = Path(__file__).resolve().parents[1]
PER_PHOTO = youcam_extra.estimated_cost("skin_analysis", len(DEFAULT_ACTIONS)) * 2 + youcam_extra.COSTS["photo_lighting"]


def spread(v):
    return {"range": max(v) - min(v), "sd": statistics.stdev(v) if len(v) > 1 else 0.0, "mean": statistics.fmean(v)}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    photos = sorted(p for p in Path(args[0]).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if len(photos) < 3:
        sys.exit("Need at least 3 same-day photos of one person.")
    est = PER_PHOTO * len(photos)
    print(f"{len(photos)} photos, estimated {est:g} YouCam units (cache hits are free).")
    if "--yes-spend" not in sys.argv:
        sys.exit("Not running. Re-run with --yes-spend to spend units.")
    cl = YouCamClient(cache_dir=ROOT / ".cache" / "youcam")
    raw, lit = [], []
    for p in photos:
        data = p.read_bytes()
        raw.append({c: v["raw_score"] for c, v in cl.analyze(data, DEFAULT_ACTIONS)["scores"].items() if v.get("raw_score") is not None})
        img = youcam_extra.photo_lighting(cl, data)["image"]
        lit.append({c: v["raw_score"] for c, v in cl.analyze(img, DEFAULT_ACTIONS)["scores"].items() if v.get("raw_score") is not None})
        print("done", p.name)
    rows, rr, lr, drift = [], [], [], 0.0
    for c in sorted(set(raw[0]) & set(lit[0])):
        a, b = spread([r[c] for r in raw if c in r]), spread([r[c] for r in lit if c in r])
        shift = abs(b["mean"] - a["mean"])
        drift = max(drift, shift)
        rr.append(a["range"]); lr.append(b["range"])
        rows.append(f"| {c} | {a['range']:.1f} | {a['sd']:.2f} | {b['range']:.1f} | {b['sd']:.2f} | {shift:.1f} |")
    mr, ml = statistics.median(rr), statistics.median(lr)
    red = (mr - ml) / mr if mr else 0.0
    adopt = red >= 0.25 and drift <= 5
    out = ["# Lighting experiment results", "", f"Photos: {len(photos)}. Rule (pre-registered, bench/lighting_README.md): adopt only if the median per-concern range drops by >= 25% and no concern's mean moves more than 5 points.", "",
           "| concern | raw range | raw SD | lit range | lit SD | mean shift |", "|---|---|---|---|---|---|", *rows, "",
           f"Median range: raw {mr:.2f}, lit {ml:.2f} ({red:+.0%} reduction). Largest mean shift: {drift:.1f}.", "",
           f"**Decision: {'ADOPT lighting normalisation' if adopt else 'KEEP IT OFF'}**"]
    (ROOT / "bench" / "lighting_RESULTS.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


main()
