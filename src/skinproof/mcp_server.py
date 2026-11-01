"""SkinProof as an MCP server (stdio), so Claude Desktop / Cursor can ask it questions.

Read-only by design. The deterministic tools never touch the network, spend no
YouCam units and call no LLM. The one tool that costs money (reading a product's
claims, about a cent of OpenRouter) is off unless SKINPROOF_MCP_ALLOW_LLM=1.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import claims as claims_mod
from . import noise
from .agent import Agent, decide
from .store import Store
from .verdict import compute_verdicts

ROOT = Path(__file__).resolve().parents[2]


def _store() -> Store:
    return Store(os.environ.get("SKINPROOF_STORE") or ROOT / "private")


def _agent(store: Store, profile_id: str, product_id: str | None = None) -> Agent:
    # Agent construction is pure: no client, no chat model, nothing is called.
    return Agent(store=store, client=None, profile_id=profile_id, product_id=product_id)


def explain_noise(profile_id: str, store: Store | None = None) -> dict[str, Any]:
    """Per-concern measurement noise and the change needed before SkinProof calls it real."""
    store = store or _store()
    p = store.get_profile(profile_id)
    if not p:
        return {"error": f"unknown profile_id {profile_id!r}"}
    cal = p.get("calibration")
    if not cal:
        return {"error": "this profile has no calibration yet", "profile_id": profile_id}
    concerns = {}
    for c, v in cal.get("concerns", {}).items():
        real = v.get("real") or {}
        concerns[c] = {"proof_threshold_points": v["threshold"], "observed_range": real.get("range"),
                       "sd": real.get("sd")}
    return {"profile_id": profile_id, "source": cal.get("source"), "n_photos": cal.get("n_photos"),
            "rule": cal.get("rule"), "note": cal.get("note"), "concerns": concerns,
            "meaning": "A skin-score change smaller than the threshold is indistinguishable from "
                       "re-photographing the same face. Only larger, confirmed changes count as proven."}


def should_i_keep_buying(profile_id: str, product_id: str | None = None,
                         store: Store | None = None) -> dict[str, Any]:
    """Deterministic KEEP / STOP / WAIT from stored check-ins. No paid calls, no LLM."""
    store = store or _store()
    try:
        a = _agent(store, profile_id, product_id)
    except KeyError:
        return {"error": f"unknown profile_id {profile_id!r}"}
    except ValueError as e:
        return {"error": str(e)}
    thr = a.thresholds()
    if not thr:
        return {"error": "no calibration for this profile; cannot separate change from noise"}
    v = compute_verdicts(a.s.checkins, thr, a.s.product)
    vlist = v["concerns"] if v.get("ready") else []
    w = a.weeks_elapsed()
    cached = a.s.product.get("claims_cache")
    checks = claims_mod.compare(cached.get("claims", []), vlist, w) if cached and vlist else None
    rule = decide(vlist, checks, w)
    return {"product": a.s.product["name"], "weeks_elapsed": w, **rule,
            "verdicts": [{k: c[k] for k in ("concern", "verdict", "delta", "threshold", "sentence")} for c in vlist],
            "claim_checks": [{k: c.get(k) for k in ("concern", "status", "sentence")} for c in checks or []],
            "paid_calls": 0, "llm_calls": 0}


def read_claims(text: str | None = None, url: str | None = None) -> dict[str, Any]:
    """Extract measurable efficacy claims from product text/URL. Costs ~1 cent; opt-in."""
    if os.environ.get("SKINPROOF_MCP_ALLOW_LLM") != "1":
        return {"error": "disabled: this tool calls a paid LLM. Set SKINPROOF_MCP_ALLOW_LLM=1 to enable it."}
    if not (text or url):
        return {"error": "provide text or url"}
    try:
        return claims_mod.extract_claims(text=text, url=url)
    except Exception as e:  # network/LLM errors become tool results, not crashes
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def demo_runs() -> list[dict[str, Any]]:
    """The public demo agent traces shipped in demo/ (decision plus the tool calls it made)."""
    out = []
    for name in ("agent_trace.json", "agent_trace_live_synthetic.json", "agent_trace_live_volunteer.json"):
        f = ROOT / "demo" / name
        if not f.is_file():
            continue
        t = json.loads(f.read_text())
        evs = t.get("events", [])
        final = next((e for e in reversed(evs) if e.get("type") == "final"), {})
        d = final.get("data", {})
        out.append({"file": name, "run_id": t.get("run_id"), "model": t.get("model"), "sample": t.get("sample"),
                    "decision": d.get("decision"), "headline": d.get("headline"),
                    "units_spent": d.get("units_spent"),
                    "steps": [e.get("summary") for e in evs if e.get("type") not in ("start", "final")]})
    return out


def build_server():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("skinproof")

    @mcp.tool(name="skinproof_explain_noise")
    def _explain(profile_id: str) -> dict:
        """Explain a profile's measured skin-scan noise: how many points a score must move to count as real change."""
        return explain_noise(profile_id)

    @mcp.tool(name="skinproof_should_i_keep_buying")
    def _keep(profile_id: str, product_id: str | None = None) -> dict:
        """KEEP / STOP / WAIT for a skincare product, from stored check-ins and the noise rule (free, deterministic)."""
        return should_i_keep_buying(profile_id, product_id)

    @mcp.tool(name="skinproof_read_claims")
    def _claims(text: str | None = None, url: str | None = None) -> dict:
        """Extract measurable efficacy claims from product text or a product URL (paid LLM, needs SKINPROOF_MCP_ALLOW_LLM=1)."""
        return read_claims(text, url)

    @mcp.tool(name="skinproof_demo_runs")
    def _demo() -> list:
        """The public demo agent runs: decision, headline and the tool steps taken."""
        return demo_runs()

    return mcp


def run_stdio() -> None:
    build_server().run(transport="stdio")
