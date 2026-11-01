"""SkinProof agent: an LLM-driven tool loop that answers "keep buying this product?".

The model chooses which tools to call; code enforces everything that matters:
  * hard YouCam unit budget and LLM dollar cap (budget.py), checked before each call
  * max 12 steps; if the model never calls finish, a deterministic fallback decides
  * decide() is the deterministic rule; if the model contradicts it, both are shown
  * only raw_score is used; changes count only when they beat measured noise (verdict.py)

run() is a generator of trace events (see PLAN.md "Agent trace events").

DECISION RULE (decide), applied to claimed concerns. Only PROVEN verdicts (beyond noise on the
two most recent check-ins, same direction) can produce STOP or KEEP:
  1. any claimed concern WORSE (proven), or claim CONTRADICTED -> STOP
  2. else any claimed concern IMPROVED (proven) / claim SUPPORTED -> KEEP
  2b. else any claimed concern LIKELY_* (one reading beyond noise) / claim NEEDS_CONFIRMATION
      -> WAIT 2 weeks and re-check to confirm (never STOP or KEEP on one reading)
  3. else any claim TOO_EARLY (timeframe not reached) -> WAIT n, n = weeks left (min 1)
  4. else (claims NOT_YET_SHOWN past their timeframe, or nothing measurable at >= 8 weeks) -> STOP
  5. else (nothing measurable and < 8 weeks) -> WAIT for the remainder of 8 weeks
  Fewer than 2 usable check-ins, or a photo that must be retaken -> WAIT.
"""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import noise
from .budget import BudgetExceeded, UnitBudget
from .store import Store
from .verdict import IMPROVED, LIKELY, REBUY_WEEKS, WORSE, compute_verdicts, valid_checkins
from .youcam import CONCERN_LABELS, DEFAULT_ACTIONS, YouCamError

MODEL = "google/gemini-3.8-flash"
MAX_STEPS = 12
SIM_STRENGTH = 0.5
DEFAULT_COST = {"skin_analysis": 12, "skin_tone": 20, "fitzpatrick": 10, "face_attributes": 10,
                "skin_simulation": 4, "photo_lighting": 2}
# optional framing check against a cached baseline face_aspect_ratio
MAX_RATIO_DIFF = 0.15

SYSTEM_PROMPT = """You are SkinProof, an honest skincare evidence agent. Your goal: answer "should this person keep buying this product?" for ONE product, using measurements on THEIR face.

Rules you must follow:
- Measure before you judge. Call get_context first. Do not call finish until compare_with_noise has run (the only exception: the photo is inconsistent and must be retaken).
- A change is real only when it beats the user's own measured noise (compare_with_noise tells you). Never call a smaller change an improvement.
- Confirmation rule: a change beyond noise on ONE check-in is only LIKELY_IMPROVED or LIKELY_WORSE and needs confirmation. It is proven (IMPROVED or WORSE) only when the two most recent check-ins are both beyond noise in the same direction. Never decide STOP or KEEP from a LIKELY verdict: finish with WAIT (1 to 2 weeks), name the concern, and say to re-check to confirm. A head turn of a few degrees can move a score by several points, so one reading is never enough.
- Many concerns are tested at once, so some will cross the line by chance; that is why confirmation is required.
- Never invent, estimate or average a score. Only quote numbers returned by tools. There is no overall progress score.
- Read the product's claims (read_product_claims) and check them against the measurements (check_claims) before deciding.
- Skin tone and Fitzpatrick type are context only, never a verdict.
- Any simulation is a picture of what the CLAIM would look like, not the user's result: label it as a simulation.
- Units are limited; each tool says what it costs. Do not repeat a call; results are cached.
- If a new photo is supplied, run check_photo_consistency before analyze_skin. If it says retake, do not measure: finish with WAIT and wait_weeks 0 and say to retake the photo.
- Finish with the finish tool: decision KEEP, STOP or WAIT (with wait_weeks), a one-sentence headline, and short reasons grounded in tool results.
Use at most %d steps. Be brief; do not write long prose between tool calls.""" % MAX_STEPS


def _tool(name: str, desc: str, props: dict | None = None, required: list | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


TOOLS = [
    _tool("get_context", "Profile, calibration noise thresholds, product (name, start date, claimed concerns), check-in history with dates and raw scores, weeks elapsed, units left. Free."),
    _tool("check_photo_consistency", "Photo quality check of the NEW photo (Face Attributes API: face found, frontal, head angle, lighting, face size; 10 units, free if cached). Recommends a retake if any is not 'good'."),
    _tool("analyze_skin", "Skin analysis of the new check-in photo, raw scores only; stores the check-in (about 12 units)."),
    _tool("skin_context", "Skin tone and Fitzpatrick type, context only (about 30 units, optional: skip unless the budget clearly allows it)."),
    _tool("compare_with_noise", "Noise-gated verdict per concern over all check-ins. Free. Needs 2+ usable check-ins and a calibration. Verdicts: IMPROVED/WORSE are proven (last two check-ins both beyond noise, same direction); LIKELY_IMPROVED/LIKELY_WORSE need confirmation (one check-in beyond noise); NO_PROVEN_CHANGE."),
    _tool("read_product_claims", "Extract the product's claimed benefits (concern, timeframe) from its stored text, URL or label photo. No units."),
    _tool("check_claims", "Compare the product's claims with the verdicts and weeks elapsed. Free. Statuses: SUPPORTED/CONTRADICTED (proven only), NEEDS_CONFIRMATION (likely, one reading), NOT_YET_SHOWN, TOO_EARLY, NOT_MEASURABLE. Run after compare_with_noise and read_product_claims."),
    _tool("simulate_claimed_result", "Skin Simulation of what the claimed improvement for one concern would look like (about 4 units). Always a simulation, not the user's result.",
          {"concern": {"type": "string", "description": "A claimed concern, e.g. pore, redness, texture, wrinkle."}}, ["concern"]),
    _tool("finish", "End the run with the final decision.", {
        "decision": {"type": "string", "enum": ["KEEP", "STOP", "WAIT"]},
        "wait_weeks": {"type": "integer", "description": "Weeks to wait before re-checking (WAIT only)."},
        "headline": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}}},
        ["decision", "headline", "reasons"]),
]
PHOTO_TOOLS = {"check_photo_consistency", "analyze_skin", "skin_context"}


# ---------------------------------------------------------------- deterministic rule

def _matches(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    strip = lambda s: s[3:] if s.startswith("hd_") else s
    return strip(a) == strip(b)


def decide(verdicts: list[dict], claim_checks: list[dict] | None, weeks_elapsed: float | None) -> dict:
    """Deterministic KEEP/STOP/WAIT. verdicts = compute_verdicts()["concerns"]. See module docstring."""
    weeks = weeks_elapsed or 0.0
    if not verdicts:
        return {"decision": "WAIT", "wait_weeks": 2, "rule": "R0",
                "headline": "Not enough usable check-ins to decide yet.",
                "reasons": ["Need at least 2 usable check-ins and a calibration before comparing."]}
    checks = [c for c in (claim_checks or []) if c.get("status") != "NOT_MEASURABLE"]
    worse, better, likely, early, notyet = [], [], [], [], []
    if checks:
        for c in checks:
            v = next((x for x in verdicts if _matches(x["concern"], c.get("concern"))), None)
            vs, st = (v or {}).get("verdict"), c.get("status")
            name = CONCERN_LABELS.get(c.get("concern"), c.get("concern"))
            if vs == WORSE or st == "CONTRADICTED":
                worse.append(name)
            elif vs == IMPROVED or st == "SUPPORTED":
                better.append(name)
            elif vs in LIKELY or st == "NEEDS_CONFIRMATION":
                likely.append(f"{name} ({'up' if vs == 'LIKELY_IMPROVED' or (vs is None and ((v or {}).get('measured') or {}).get('delta', 0) > 0) else 'down'})")
            elif st == "TOO_EARLY":
                early.append(c)
            else:
                notyet.append(name)
    else:  # no claim checks: use the concerns the user marked as targeted
        for v in verdicts:
            if v.get("targeted"):
                if v["verdict"] == WORSE:
                    worse.append(v["label"])
                elif v["verdict"] == IMPROVED:
                    better.append(v["label"])
                elif v["verdict"] in LIKELY:
                    likely.append(f"{v['label']} ({'up' if v['verdict'] == 'LIKELY_IMPROVED' else 'down'})")
                elif weeks >= REBUY_WEEKS:
                    notyet.append(v["label"])
    if worse:
        return {"decision": "STOP", "wait_weeks": None, "rule": "R1",
                "headline": f"Stop: {', '.join(worse)} got worse or the claim was contradicted, beyond measured noise.",
                "reasons": [f"{n}: worse or contradicted beyond your measured noise." for n in worse]}
    if better:
        return {"decision": "KEEP", "wait_weeks": None, "rule": "R2",
                "headline": f"Keep: {', '.join(better)} improved beyond your measured noise.",
                "reasons": [f"{n}: improvement is larger than your own measurement noise." for n in better]}
    if likely:
        return {"decision": "WAIT", "wait_weeks": 2, "rule": "R2b",
                "headline": f"Wait 2 weeks and re-check to confirm: {', '.join(likely)} moved beyond noise on one check-in only.",
                "reasons": [f"{n}: one check-in beyond your noise is 'likely', not proven. Take another in 1 to 2 weeks, "
                            f"same light and pose; two in a row in the same direction makes it proven." for n in likely]}
    if early:
        left = [(c.get("timeframe_weeks") or REBUY_WEEKS) - weeks for c in early]
        n = max(1, math.ceil(max(left)))
        return {"decision": "WAIT", "wait_weeks": n, "rule": "R3",
                "headline": f"Wait {n} weeks: the product's own timeframe has not passed yet.",
                "reasons": [f"{CONCERN_LABELS.get(c.get('concern'), c.get('concern'))}: claimed in "
                            f"{c.get('timeframe_weeks')} weeks, {weeks:.0f} elapsed." for c in early]}
    if notyet or weeks >= REBUY_WEEKS:
        return {"decision": "STOP", "wait_weeks": None, "rule": "R4",
                "headline": "Stop: no proven change on the claimed concerns after the claimed time.",
                "reasons": [f"{n}: no change larger than your measurement noise." for n in notyet]
                           or [f"No proven change after {weeks:.0f} weeks."]}
    n = max(1, math.ceil(REBUY_WEEKS - weeks))
    return {"decision": "WAIT", "wait_weeks": n, "rule": "R5",
            "headline": f"Wait {n} weeks: too early to judge.",
            "reasons": [f"Only {weeks:.0f} weeks elapsed and nothing measurable yet."]}


# ---------------------------------------------------------------- run state

@dataclass
class ToolOut:
    compact: dict           # what the model sees
    full: dict | None = None  # what the trace keeps
    summary: str = ""
    error: bool = False


@dataclass
class RunState:
    profile: dict
    product: dict
    photo: bytes | None
    when: str
    checkins: list = field(default_factory=list)
    verdict_result: dict | None = None
    claims: dict | None = None
    claim_checks: list | None = None
    sim_url: str | None = None
    retake: bool = False
    consistency: dict | None = None
    new_ratio: float | None = None
    tone: dict | None = None
    final: dict | None = None


class Agent:
    def __init__(self, *, store: Store, client: Any, profile_id: str, product_id: str | None = None,
                 photo: bytes | None = None, when: str | None = None, chat: Callable | None = None,
                 extra: Any = None, claims: Any = None, budget: UnitBudget | None = None,
                 model: str = MODEL, runs_dir: Path | None = None, max_steps: int = MAX_STEPS):
        profile = store.get_profile(profile_id)
        if not profile:
            raise KeyError(profile_id)
        prods = profile["products"]
        product = next((p for p in prods if p["id"] == product_id), None) or (prods[-1] if prods else None)
        if product is None:
            raise ValueError("Add a product first.")
        self.store, self.client, self.pid = store, client, profile_id
        self.chat, self.extra, self.claims_mod = chat, extra, claims
        self.model, self.max_steps = model, max_steps
        self.budget = budget or UnitBudget()
        self.runs_dir = runs_dir or store.root / "runs"
        self.run_id = uuid.uuid4().hex[:20]
        self.when = when or date.today().isoformat()
        self.s = RunState(profile=profile, product=product, photo=photo, when=self.when,
                          checkins=list(profile["checkins"]))
        self.events: list[dict] = []
        self.cache: dict[str, ToolOut] = {}
        self.step = 0

    # ---- lazy deps
    def _chat(self):
        if self.chat is None:
            from . import llm
            self.chat = llm.chat
        return self.chat

    def _extra(self):
        if self.extra is None:
            from . import youcam_extra
            self.extra = youcam_extra
        return self.extra

    def _claims(self):
        if self.claims_mod is None:
            from . import claims
            self.claims_mod = claims
        return self.claims_mod

    # ---- events
    def _ev(self, type_: str, summary: str, **kw) -> dict:
        e = {"type": type_, "step": self.step, "summary": summary,
             "units_spent": self.budget.spent, "units_left_in_budget": self.budget.left,
             "llm_cost": round(self.budget.llm_cost, 6), **{k: v for k, v in kw.items() if v is not None}}
        self.events.append(e)
        return e

    # ---- helpers
    def weeks_elapsed(self) -> float | None:
        start = self.s.product.get("start_date")
        if not start:
            return None
        ok = valid_checkins(self.s.checkins)
        end = ok[-1]["date"] if ok else self.when
        try:
            return round(max(0.0, (date.fromisoformat(end) - date.fromisoformat(start)).days / 7), 1)
        except ValueError:
            return None

    def thresholds(self) -> dict[str, float]:
        cal = self.s.profile.get("calibration")
        return noise.thresholds(cal) if cal else {}

    def cost_of(self, feature: str) -> float:
        try:
            c = self._extra().estimated_cost(feature)
            if c:
                return float(c)
        except Exception:
            pass
        return float(DEFAULT_COST.get(feature, 8))

    def paid(self, feature: str, fn: Callable[[], dict], estimate: float | None = None) -> dict:
        """Run a paid call through the budget. Result's 'units' (default estimate) is committed."""
        est = self.cost_of(feature) if estimate is None else estimate
        self.budget.reserve(feature, est)
        try:
            res = fn()
        except BaseException:
            self.budget.release()
            raise
        self.budget.commit(float(res.get("units", est)) if isinstance(res, dict) else est)
        return res

    # ---- tools
    def t_get_context(self) -> ToolOut:
        p, prod = self.s.profile, self.s.product
        cal = self.thresholds()
        hist = [{"date": c["date"], "scores": c.get("scores"), "error": (c.get("error") or {}).get("message")}
                for c in sorted(self.s.checkins, key=lambda c: (c.get("date", ""), c.get("seq", 0)))]
        d = {"profile": p["name"], "noise_thresholds": cal, "calibrated": bool(cal),
             "product": {k: prod.get(k) for k in ("name", "start_date", "concerns")},
             "has_claim_inputs": bool(prod.get("claim_text") or prod.get("claim_url") or prod.get("claim_image")),
             "checkins": hist, "weeks_elapsed": self.weeks_elapsed(),
             "new_photo_supplied": self.s.photo is not None, "new_photo_date": self.when,
             "units_left": self.budget.left}
        return ToolOut(d, d, f"{len(hist)} check-ins, {self.weeks_elapsed()} weeks on product, "
                              f"{'calibrated' if cal else 'NOT calibrated'}, {self.budget.left:g} units left")

    def t_check_photo_consistency(self) -> ToolOut:
        """One Face Attributes call on the NEW photo only (<=5 features = 10 units)."""
        if self.s.photo is None:
            return ToolOut({"error": "no new photo supplied"}, summary="No new photo", error=True)
        fa = self._extra().face_attributes
        b = self.paid("face_attributes", lambda: fa(self.client, self.s.photo),
                      estimate=self._extra().estimated_cost("face_attributes", 5)
                      if hasattr(self._extra(), "estimated_cost") else None)
        q = b.get("face_quality") or {k: b.get(k) for k in ("area", "frontal", "lighting", "faceangle")}
        issues = []
        if q.get("has_face") is False:
            issues.append("no face was found in the photo")
        for key, words in (("frontal", "the face is not facing the camera straight on"),
                           ("faceangle", "the head angle is not straight enough"),
                           ("lighting", "the lighting is not good enough"),
                           ("area", "the face does not fill enough of the frame")):
            val = q.get(key)
            if val is not None and str(val).lower() != "good":
                issues.append(f"{words} (rated '{val}')")
        ratio = b.get("face_ratio")
        self.s.new_ratio = ratio
        base = next((c for c in valid_checkins(self.s.checkins) if c.get("face_ratio")), None)
        ratio_note = None
        if ratio and base:
            diff = abs(ratio - base["face_ratio"]) / base["face_ratio"]
            ratio_note = f"face aspect ratio differs from baseline by {diff:.0%}"
            if diff > MAX_RATIO_DIFF:
                issues.append(f"{ratio_note} (limit {MAX_RATIO_DIFF:.0%}); the framing or pose changed")
        self.s.retake = bool(issues)
        res = {"consistent": not issues, "issues": issues, "quality": q,
               "recommendation": "retake" if issues else "ok to measure"}
        if ratio_note:
            res["ratio_note"] = ratio_note
        self.s.consistency = res
        return ToolOut(res, {**res, "face_ratio": ratio},
                       "Photo quality is good" if not issues else "Retake recommended: " + "; ".join(issues))

    def t_analyze_skin(self) -> ToolOut:
        if self.s.photo is None:
            return ToolOut({"error": "no new photo supplied"}, summary="No new photo", error=True)
        if self.s.retake:
            return ToolOut({"error": "photo is inconsistent with baseline; recommend retake, do not measure"},
                           summary="Blocked: retake first", error=True)
        rec: dict = {"date": self.when}
        try:
            res = self.paid("skin_analysis", lambda: (lambda r: {**r, "units": 0 if r.get("cached") else self.cost_of("skin_analysis")})(
                self.client.analyze(self.s.photo, DEFAULT_ACTIONS)))
            rec["photo"] = self.store.save_photo(self.pid, "checkin", self.s.photo)
            if self.s.new_ratio:
                rec["face_ratio"] = self.s.new_ratio
            rec["scores"] = {c: v["raw_score"] for c, v in res["scores"].items() if v.get("raw_score") is not None}
            rec["cached"] = res.get("cached", False)
        except YouCamError as e:
            if not e.is_image_error:
                raise
            rec["error"] = {"code": e.code, "message": e.friendly}
        saved = self.store.add_checkin(self.pid, rec)
        self.s.checkins.append(saved)
        if "error" in rec:
            return ToolOut({"error": rec["error"]["message"], "action": "retake the photo"},
                           {"checkin": saved}, "Photo rejected: " + rec["error"]["message"], error=True)
        return ToolOut({"raw_scores": rec["scores"], "cached": rec["cached"]}, {"checkin": saved},
                       f"Analysed {len(rec['scores'])} concerns" + (" (cached)" if rec["cached"] else ""))

    def t_skin_context(self) -> ToolOut:
        if self.s.photo is None:
            return ToolOut({"error": "no new photo supplied"}, summary="No new photo", error=True)
        ex = self._extra()
        tone = self.paid("skin_tone", lambda: ex.skin_tone(self.client, self.s.photo))
        fitz = self.paid("fitzpatrick", lambda: ex.fitzpatrick(self.client, self.s.photo))
        d = {"skin_tone": tone.get("tone"), "fitzpatrick": fitz.get("type"), "note": "context only, not a verdict"}
        self.s.tone = d
        return ToolOut(d, d, f"Tone {d['skin_tone']}, Fitzpatrick {d['fitzpatrick']} (context only)")

    def t_compare_with_noise(self) -> ToolOut:
        thr = self.thresholds()
        if not thr:
            return ToolOut({"error": "no calibration for this profile; cannot separate change from noise"},
                           summary="No calibration", error=True)
        v = compute_verdicts(self.s.checkins, thr, self.s.product)
        self.s.verdict_result = v
        if not v["ready"]:
            return ToolOut({"ready": False, "message": v["message"]}, v, v["message"])
        compact = {"method": v["method"], "weeks_on_product": v["weeks_on_product"], "concerns": [
            {"concern": c["concern"], "verdict": c["verdict"], "delta": c["delta"],
             "noise_threshold": round(c["threshold"], 1), "targeted": c["targeted"],
             "recent_deltas": c.get("recent_deltas")} for c in v["concerns"]],
            "concerns_tested": v["concerns_tested"], "note": v["multiple_comparison_note"]}
        proven = sum(c["verdict"] in ("IMPROVED", "WORSE") for c in v["concerns"])
        likely = sum(c["verdict"] in LIKELY for c in v["concerns"])
        return ToolOut(compact, v, f"{proven} proven, {likely} likely (needs confirmation) of "
                                   f"{len(v['concerns'])} concerns tested")

    def t_read_product_claims(self) -> ToolOut:
        if self.s.claims is not None:
            return ToolOut(self._claims_compact(self.s.claims), self.s.claims, "Claims (cached for product)")
        prod = self.s.product
        cached = prod.get("claims_cache")
        if cached:
            self.s.claims = cached
            return ToolOut(self._claims_compact(cached), cached, f"{len(cached.get('claims', []))} claims (stored)")
        text, url, img = prod.get("claim_text"), prod.get("claim_url"), None
        if prod.get("claim_image"):
            img = (self.store.root / prod["claim_image"]).read_bytes()
        if text or url or img:
            self.budget.check_llm()
            res = self._claims().extract_claims(text=text, url=url, image=img)
            self.budget.add_llm_cost(res.get("cost") if isinstance(res, dict) else 0)
        else:  # nothing to read: fall back to what the user declared, clearly marked
            res = {"product": prod["name"], "source": "declared", "claims": [
                {"concern": c, "direction": "improve", "timeframe_weeks": None,
                 "quote": f"(declared by user) {CONCERN_LABELS.get(c, c)}"} for c in prod.get("concerns", [])]}
        self.s.claims = res
        try:
            self.store._mutate(self.pid, lambda p: next(x for x in p["products"] if x["id"] == prod["id"]).__setitem__("claims_cache", res))
        except Exception:
            pass
        return ToolOut(self._claims_compact(res), res, f"{len(res.get('claims', []))} claims from {res.get('source')}")

    @staticmethod
    def _claims_compact(res: dict) -> dict:
        return {"source": res.get("source"), "claims": [
            {k: c.get(k) for k in ("concern", "timeframe_weeks", "quote")} for c in res.get("claims", [])]}

    def t_check_claims(self) -> ToolOut:
        if self.s.verdict_result is None or not self.s.verdict_result.get("ready"):
            return ToolOut({"error": "run compare_with_noise first (and it must be ready)"}, summary="No verdicts yet", error=True)
        if self.s.claims is None:
            return ToolOut({"error": "run read_product_claims first"}, summary="No claims yet", error=True)
        w = self.weeks_elapsed()
        cm = self._claims()
        verdicts = self.s.verdict_result["concerns"]
        try:
            out = cm.compare(self.s.claims.get("claims", []), verdicts, w)
        except TypeError:
            out = cm.compare(self.s.claims.get("claims", []), verdicts)
        self.s.claim_checks = out
        compact = [{k: c.get(k) for k in ("concern", "status", "timeframe_weeks", "weeks_elapsed")} for c in out]
        counts: dict[str, int] = {}
        for c in out:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        return ToolOut({"checks": compact}, {"checks": out}, ", ".join(f"{n} {k}" for k, n in counts.items()) or "no claims")

    def t_simulate_claimed_result(self, concern: str = "") -> ToolOut:
        concern = (concern or "").removeprefix("hd_")
        if concern not in CONCERN_LABELS:
            return ToolOut({"error": f"unknown concern '{concern}'", "valid": list(DEFAULT_ACTIONS)}, summary="Unknown concern", error=True)
        img = self.s.photo
        if img is None:
            ok = [c for c in valid_checkins(self.s.checkins) if c.get("photo")]
            if not ok:
                return ToolOut({"error": "no photo available to simulate on"}, summary="No photo", error=True)
            img = (self.store.root / ok[-1]["photo"]).read_bytes()
        ex = self._extra()
        res = self.paid("skin_simulation", lambda: ex.skin_simulation(self.client, img, {concern: SIM_STRENGTH}))
        rel = self.store.save_photo(self.pid, "simulation", res["image"])
        url = f"/api/files/{rel}"
        self.s.sim_url = url
        d = {"url": url, "label": "Simulation, not your result", "concern": concern}
        return ToolOut({"saved": True, "label": d["label"], "concern": concern}, d,
                       f"Simulation of claimed {CONCERN_LABELS[concern]} improvement (not your result)")

    def t_finish(self, decision: str = "", wait_weeks: Any = None, headline: str = "", reasons: Any = None) -> ToolOut:
        decision = str(decision).upper().strip()
        if decision not in ("KEEP", "STOP", "WAIT"):
            return ToolOut({"error": "decision must be KEEP, STOP or WAIT"}, summary="Bad decision", error=True)
        if self.s.verdict_result is None and not self.s.retake:
            return ToolOut({"error": "measure before judging: call compare_with_noise first"}, summary="Refused: no measurement yet", error=True)
        try:
            ww = int(wait_weeks) if wait_weeks is not None else None
        except (TypeError, ValueError):
            ww = None
        if decision == "WAIT" and ww is None:
            ww = 0 if self.s.retake else 2
        reasons = [str(r) for r in reasons] if isinstance(reasons, list) else ([str(reasons)] if reasons else [])
        self.s.final = {"decision": decision, "wait_weeks": ww if decision == "WAIT" else None,
                        "headline": str(headline), "reasons": reasons}
        return ToolOut({"ok": True}, self.s.final, f"Decision: {decision}")

    # ---- dispatch
    def call_tool(self, name: str, args: dict) -> ToolOut:
        fn = getattr(self, "t_" + name, None)
        if fn is None or name not in {t["function"]["name"] for t in TOOLS}:
            return ToolOut({"error": f"unknown tool '{name}'"}, summary="Unknown tool", error=True)
        key = name + json.dumps(args, sort_keys=True, default=str)
        if name != "finish" and key in self.cache:
            prev = self.cache[key]
            return ToolOut({**prev.compact, "note": "repeated call; cached result, no units spent"},
                           prev.full, prev.summary + " (cached)", prev.error)
        try:
            out = fn(**args)
        except BudgetExceeded as e:
            return ToolOut({"error": "budget_exceeded", "message": str(e)}, summary=str(e), error=True)
        except TypeError as e:
            return ToolOut({"error": f"bad arguments: {e}"}, summary="Bad arguments", error=True)
        except YouCamError as e:
            return ToolOut({"error": e.friendly}, summary=e.friendly, error=True)
        except Exception as e:  # tool failures go back to the model, never crash the run
            msg = f"{type(e).__name__}: {str(e)[:200]}"
            return ToolOut({"error": msg}, summary=msg, error=True)
        if name != "finish" and not out.error:
            self.cache[key] = out
        return out

    def tool_specs(self) -> list[dict]:
        return [t for t in TOOLS if self.s.photo is not None or t["function"]["name"] not in PHOTO_TOOLS]

    # ---- final
    def _final_data(self, fallback: bool, note: str | None = None) -> dict:
        v = self.s.verdict_result
        if v is None and self.thresholds():
            v = compute_verdicts(self.s.checkins, self.thresholds(), self.s.product)
        vlist = (v or {}).get("concerns", []) if v and v.get("ready") else []
        w = self.weeks_elapsed()
        rule = decide(vlist, self.s.claim_checks, w)
        if self.s.retake and self.s.final is None:
            rule = {"decision": "WAIT", "wait_weeks": 0, "rule": "R-retake",
                    "headline": "Retake the photo: pose or framing differs too much from your baseline.",
                    "reasons": [i for i in (self.s.consistency or {}).get("issues", [])]}
        m = self.s.final
        chosen = m or rule
        data = {"decision": chosen["decision"], "wait_weeks": chosen.get("wait_weeks"),
                "headline": chosen["headline"], "reasons": chosen["reasons"],
                "verdicts": vlist, "concerns_tested": (v or {}).get("concerns_tested"),
                "multiple_comparison_note": (v or {}).get("multiple_comparison_note"), "claims": self.s.claim_checks or [],
                "simulation_image": self.s.sim_url,
                "simulation_label": "Simulation, not your result" if self.s.sim_url else None,
                "fallback": fallback, "rule_decision": {k: rule[k] for k in ("decision", "wait_weeks", "rule", "headline")},
                "weeks_elapsed": w, "run_id": self.run_id, "model": self.model,
                "disagreement": None, "units_spent": self.budget.spent, "llm_cost": round(self.budget.llm_cost, 6)}
        if fallback:
            data["fallback_reason"] = note
        if m and vlist and m["decision"] != rule["decision"]:
            data["disagreement"] = {
                "model": {k: m[k] for k in ("decision", "wait_weeks", "headline")},
                "rule": {k: rule[k] for k in ("decision", "wait_weeks", "headline", "rule")},
                "note": f"The model chose {m['decision']} but the evidence rule ({rule['rule']}) gives "
                        f"{rule['decision']}. Both are shown; the model's decision is not silently overridden."}
        return {k: v2 for k, v2 in data.items() if v2 is not None or k in ("wait_weeks", "disagreement")}

    def save_trace(self) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.runs_dir / f"{self.run_id}.json"
        final = next((e for e in reversed(self.events) if e["type"] == "final"), None)
        path.write_text(json.dumps({"run_id": self.run_id, "profile_id": self.pid, "sample": False,
                                    "model": self.model, "date": self.when,
                                    "fallback": bool(final and final["data"].get("fallback")),
                                    "events": self.events}, indent=1, default=str))
        return path

    # ---- the loop
    def run(self):
        try:
            yield from self._run()
        finally:
            try:
                self.save_trace()
            except Exception:
                pass

    def _run(self):
        s = self.s
        yield self._ev("start", f"Deciding whether to keep {s.product['name']}",
                       data={"run_id": self.run_id, "product": s.product["name"], "model": self.model,
                             "max_units": self.budget.max_units, "max_steps": self.max_steps,
                             "max_llm_usd": self.budget.max_llm_usd, "has_new_photo": s.photo is not None})
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Product: {s.product['name']}. "
             f"{'A new check-in photo is supplied.' if s.photo else 'No new photo this time; use existing check-ins.'} "
             "Decide KEEP, STOP or WAIT."}]
        fallback_note = None
        for step in range(1, self.max_steps + 1):
            self.step = step
            try:
                self.budget.check_llm()
                r = self._chat()(messages, tools=self.tool_specs(), model=self.model, max_tokens=1500, temperature=0.2)
            except BudgetExceeded as e:
                fallback_note = str(e)
                yield self._ev("error", str(e))
                break
            except Exception as e:
                fallback_note = f"LLM call failed: {type(e).__name__}"
                yield self._ev("error", fallback_note + f": {str(e)[:200]}")
                break
            self.budget.add_llm_cost(r.get("cost"))
            msg = r.get("message") or {}
            text = (msg.get("content") or "").strip() if isinstance(msg.get("content"), str) else ""
            calls = msg.get("tool_calls") or []
            if text:
                yield self._ev("thought", text[:600])
            messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls:
                messages.append({"role": "user", "content": "You must call a tool. Use finish when you are ready to decide."})
                if not text:
                    yield self._ev("thought", "(model replied without a tool call; nudged)")
                continue
            for tc in calls:
                fn = (tc.get("function") or {})
                name, raw = fn.get("name") or "", fn.get("arguments")
                args, bad = {}, None
                if isinstance(raw, dict):
                    args = raw
                elif raw:
                    try:
                        args = json.loads(raw)
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                    except ValueError as e:
                        bad = f"Malformed tool arguments: {e}"
                yield self._ev("tool_call", f"Calling {name}", tool=name, args=args)
                out = ToolOut({"error": bad}, summary="Malformed arguments", error=True) if bad else self.call_tool(name, args)
                yield self._ev("tool_result", out.summary, tool=name, data={"result": out.full or out.compact, "error": out.error})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "name": name,
                                 "content": json.dumps(out.compact, default=str)})
                if name == "finish" and s.final:
                    break
            if s.final:
                break
        if s.final:
            yield self._ev("final", s.final["headline"], data=self._final_data(False))
        else:
            data = self._final_data(True, fallback_note or f"model did not call finish within {self.max_steps} steps")
            yield self._ev("final", "Fallback decision (model did not finish): " + data["headline"], data=data)


def sse(event: dict) -> str:
    return "data: " + json.dumps(event, default=str) + "\n\n"
