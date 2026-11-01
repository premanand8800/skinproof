"""Measurement-noise calibration.

Rule (the heart of SkinProof)
-----------------------------
For each concern we take N photos of the SAME face on the SAME day under
different light, angle and distance. The skin did not change, so any spread in
raw_score is measurement noise. Per concern:

    proof_threshold = max(observed range (max - min), 2 * sample SD, FLOOR)

FLOOR is 3 points so a lucky set of near-identical retakes cannot make us
over-confident. A later change only counts as "proven" when it is larger than
this threshold.

Synthetic perturbations of one photo (brightness, zoom, JPEG, rotation) give a
cheaper lower bound on the same quantity. When both are present we use the
larger threshold per concern (real retakes are the more honest estimate).
"""
from __future__ import annotations

import io
import statistics
from typing import Any

from PIL import Image, ImageEnhance

FLOOR = 3.0
MIN_RETAKES = 3


def _spread(values: list[float]) -> dict[str, float]:
    rng = max(values) - min(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return {"n": len(values), "mean": statistics.fmean(values), "sd": sd,
            "range": rng, "threshold": max(rng, 2 * sd, FLOOR)}


def estimate_noise(per_photo_scores: list[dict[str, float]], *, floor: float = FLOOR) -> dict[str, dict]:
    """per_photo_scores: one {concern: raw_score} dict per accepted photo."""
    by_concern: dict[str, list[float]] = {}
    for s in per_photo_scores:
        for c, v in s.items():
            if v is not None:
                by_concern.setdefault(c, []).append(float(v))
    out = {}
    for c, vals in by_concern.items():
        if len(vals) < 2:
            continue
        sp = _spread(vals)
        sp["threshold"] = max(sp["range"], 2 * sp["sd"], floor)
        out[c] = sp
    return out


def build_calibration(real: list[dict[str, float]] | None = None,
                      synthetic: list[dict[str, float]] | None = None) -> dict[str, Any]:
    real_n = estimate_noise(real) if real and len(real) >= MIN_RETAKES else {}
    syn_n = estimate_noise(synthetic) if synthetic and len(synthetic) >= 2 else {}
    if not real_n and not syn_n:
        raise ValueError(f"Need at least {MIN_RETAKES} usable same-day photos to calibrate")
    concerns: dict[str, dict] = {}
    for c in set(real_n) | set(syn_n):
        r, s = real_n.get(c), syn_n.get(c)
        thr = max(x["threshold"] for x in (r, s) if x)
        concerns[c] = {"threshold": round(thr, 2), "real": r, "synthetic": s}
    source = "retakes+synthetic" if real_n and syn_n else ("retakes" if real_n else "synthetic")
    return {"source": source, "n_photos": len(real or []), "floor": FLOOR, "concerns": concerns,
            "rule": "threshold = max(range, 2*SD, 3) over same-day retakes"}


def thresholds(calibration: dict[str, Any]) -> dict[str, float]:
    return {c: v["threshold"] for c, v in calibration.get("concerns", {}).items()}


def perturbations(image_bytes: bytes) -> list[tuple[str, bytes]]:
    """Synthetic variants of one photo: brightness +-15%, zoom 90-100%, JPEG q70, 3 deg rotation.

    Pure function, no network. Analysing these costs units, so callers must
    opt in explicitly (see scripts/synthetic_noise.py).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    def enc(im: Image.Image, q: int = 92) -> bytes:
        b = io.BytesIO()
        im.save(b, "JPEG", quality=q)
        return b.getvalue()

    def zoom(f: float) -> Image.Image:
        cw, ch = int(w * f), int(h * f)
        l, t = (w - cw) // 2, (h - ch) // 2
        return img.crop((l, t, l + cw, t + ch)).resize((w, h), Image.LANCZOS)

    return [
        ("brightness-15", enc(ImageEnhance.Brightness(img).enhance(0.85))),
        ("brightness+15", enc(ImageEnhance.Brightness(img).enhance(1.15))),
        ("zoom-90", enc(zoom(0.90))),
        ("zoom-95", enc(zoom(0.95))),
        ("jpeg-q70", enc(img, 70)),
        ("rotate+3", enc(img.rotate(3, resample=Image.BICUBIC, expand=False))),
        ("rotate-3", enc(img.rotate(-3, resample=Image.BICUBIC, expand=False))),
    ]
