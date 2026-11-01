"""Noise-gated verdicts. Never produces a single overall progress score."""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from .youcam import CONCERN_LABELS

IMPROVED, WORSE, NO_PROVEN = "IMPROVED", "WORSE", "NO_PROVEN_CHANGE"
REBUY_WEEKS = 8


def label(c: str) -> str:
    return CONCERN_LABELS.get(c, c.replace("_", " ").title())


def valid_checkins(checkins: list[dict]) -> list[dict]:
    """Photo-quality gate: a check-in with an image error is not counted."""
    ok = [c for c in checkins if not c.get("error") and c.get("scores")]
    return sorted(ok, key=lambda c: (c.get("date", ""), c.get("id", "")))


def _median_scores(group: list[dict], concern: str) -> float | None:
    vals = [c["scores"][concern] for c in group if concern in c["scores"] and c["scores"][concern] is not None]
    return statistics.median(vals) if vals else None


def _pts(x: float) -> str:
    x = abs(x)
    n = round(x)
    return f"{n} point{'s' if n != 1 else ''}"


def sentence(concern: str, delta: float, thr: float, verdict: str) -> str:
    name, t = label(concern), round(thr)
    if verdict == IMPROVED:
        return (f"{name}: improved by {_pts(delta)}, more than the {t}-point noise we measured "
                f"on your face. Proven.")
    if verdict == WORSE:
        return (f"{name}: worse by {_pts(delta)}, more than the {t}-point noise we measured "
                f"on your face. Proven.")
    return (f"{name}: changed by {_pts(delta) if round(abs(delta)) else '0 points'}; your photos vary by "
            f"{t} points even on the same day. No proven change yet.")


def next_step(verdict: str, targeted: bool, weeks: float | None) -> str:
    if verdict == IMPROVED:
        return ("Keep using it. This is a measurable effect on your face." if targeted
                else "Improved, though this product does not claim this concern. Keep tracking.")
    if verdict == WORSE:
        return ("Pause the product and check for irritation. If it persists, see a dermatologist."
                if targeted else "Worse beyond noise. Review anything else you changed recently.")
    if not targeted:
        return "Not a concern this product claims to target. Nothing to decide here."
    if weeks is not None and weeks >= REBUY_WEEKS:
        return (f"No proven change after {weeks:.0f} weeks. Don't rebuy yet: this product hasn't "
                f"shown a measurable effect on your face.")
    left = f" Check again in about {max(1, round(REBUY_WEEKS - (weeks or 0)))} weeks." if weeks is not None else ""
    return "Too early to call. Keep your routine steady." + left


def compute_verdicts(checkins: list[dict], thresholds: dict[str, float],
                     product: dict | None = None, today: date | None = None) -> dict[str, Any]:
    valid = valid_checkins(checkins)
    skipped = len(checkins) - len(valid)
    base = {"valid_checkins": len(valid), "skipped_checkins": skipped, "concerns": []}
    if len(valid) < 2:
        return {**base, "ready": False,
                "message": "Need at least 2 usable check-ins before SkinProof can compare. "
                           "Check-ins with photo-quality errors do not count."}
    if len(valid) >= 4:
        before, after, method = valid[:2], valid[-2:], "median of first two vs median of last two"
    else:
        before, after, method = valid[:1], valid[-1:], "first vs latest"
    claims = set((product or {}).get("concerns", []))
    weeks = None
    if product and product.get("start_date"):
        end = date.fromisoformat(valid[-1]["date"]) if not today else today
        try:
            weeks = max(0.0, (end - date.fromisoformat(product["start_date"])).days / 7)
        except ValueError:
            weeks = None
    out = []
    for c, thr in thresholds.items():
        b, a = _median_scores(before, c), _median_scores(after, c)
        if b is None or a is None:
            continue
        delta = a - b
        v = IMPROVED if delta > thr else WORSE if delta < -thr else NO_PROVEN
        targeted = c in claims
        out.append({"concern": c, "label": label(c), "baseline": b, "latest": a,
                    "delta": round(delta, 2), "threshold": thr, "verdict": v,
                    "targeted": targeted, "sentence": sentence(c, delta, thr, v),
                    "next": next_step(v, targeted, weeks)})
    out.sort(key=lambda x: (not x["targeted"], x["label"]))
    return {**base, "ready": True, "method": method, "weeks_on_product": weeks, "concerns": out}
