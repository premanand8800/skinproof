"""Noise-gated verdicts. Never produces a single overall progress score."""
from __future__ import annotations

import statistics
from datetime import date
from typing import Any

from .youcam import CONCERN_LABELS

IMPROVED, WORSE, NO_PROVEN = "IMPROVED", "WORSE", "NO_PROVEN_CHANGE"
LIKELY_IMPROVED, LIKELY_WORSE = "LIKELY_IMPROVED", "LIKELY_WORSE"
LIKELY = (LIKELY_IMPROVED, LIKELY_WORSE)
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
    if verdict == LIKELY_IMPROVED:
        return (f"{name}: up {_pts(delta)}, beyond the {t}-point noise, but on one check-in only. "
                f"Likely improved, needs confirmation: one more check-in in the same direction makes it proven.")
    if verdict == LIKELY_WORSE:
        return (f"{name}: down {_pts(delta)}, beyond the {t}-point noise, but on one check-in only. "
                f"Likely worse, needs confirmation: a head turn or different light can do this. "
                f"Re-check before acting on it.")
    return (f"{name}: changed by {_pts(delta) if round(abs(delta)) else '0 points'}; your photos vary by "
            f"{t} points even on the same day. No proven change yet.")


def next_step(verdict: str, targeted: bool, weeks: float | None) -> str:
    if verdict in LIKELY:
        return "Take another check-in in 1 to 2 weeks, same light and pose. Only a second reading confirms it."
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
    # Baseline = median of the first two counted check-ins (when 4+ exist, so the groups never
    # overlap), else the first. "Recent" = the two most recent counted check-ins (one if only 2 exist).
    if len(valid) >= 4:
        before, recent = valid[:2], valid[-2:]
        method = "baseline = median of first two; confirmed when the last two both beat the noise"
    elif len(valid) == 3:
        before, recent = valid[:1], valid[1:]
        method = "baseline = first; confirmed when the last two both beat the noise"
    else:
        before, recent = valid[:1], valid[1:]
        method = "baseline = first vs latest; one reading can only be 'likely'"
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
        b = _median_scores(before, c)
        reads = [_median_scores([r], c) for r in recent]
        if b is None or reads[-1] is None:
            continue
        deltas = [r - b for r in reads if r is not None]
        delta = deltas[-1]
        sign = 1 if delta > thr else -1 if delta < -thr else 0
        confirmed = sign != 0 and len(deltas) >= 2 and all(d * sign > thr for d in deltas[-2:])
        if sign == 0:
            v = NO_PROVEN
        elif confirmed:
            v = IMPROVED if sign > 0 else WORSE
        else:
            v = LIKELY_IMPROVED if sign > 0 else LIKELY_WORSE
        targeted = c in claims
        out.append({"concern": c, "label": label(c), "baseline": b, "latest": reads[-1],
                    "delta": round(delta, 2), "recent_deltas": [round(d, 2) for d in deltas],
                    "threshold": thr, "verdict": v, "confirmed": confirmed,
                    "targeted": targeted, "sentence": sentence(c, delta, thr, v),
                    "next": next_step(v, targeted, weeks)})
    out.sort(key=lambda x: (not x["targeted"], x["label"]))
    n = len(out)
    note = (f"{n} concerns were tested at once. At this many comparisons a lone reading can cross its "
            f"noise line by chance, so a single beyond-noise change is only 'likely' until a second "
            f"check-in confirms it in the same direction.")
    return {**base, "ready": True, "method": method, "weeks_on_product": weeks, "concerns": out,
            "concerns_tested": n, "multiple_comparison_note": note}
