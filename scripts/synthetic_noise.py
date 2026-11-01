"""Opt-in: spend units to measure synthetic noise on ONE of your own photos.

Usage: uv run python scripts/synthetic_noise.py PHOTO.jpg --yes-spend
Costs ~12 units per perturbation (7 variants = ~84 units). Prints thresholds only.
"""
import argparse
import sys

from skinproof import noise
from skinproof.youcam import YouCamClient

ap = argparse.ArgumentParser()
ap.add_argument("photo")
ap.add_argument("--yes-spend", action="store_true")
a = ap.parse_args()
if not a.yes_spend:
    sys.exit("Refusing to spend units without --yes-spend")
c = YouCamClient()
data = open(a.photo, "rb").read()
runs = [c.analyze(data)] + [c.analyze(b) for _, b in noise.perturbations(data)]
cal = noise.build_calibration(synthetic=[{k: v["raw_score"] for k, v in r["scores"].items()} for r in runs])
for k, v in cal["concerns"].items():
    print(f"{k:16s} threshold {v['threshold']}")
