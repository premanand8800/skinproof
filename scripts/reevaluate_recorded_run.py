"""Re-evaluate the recorded live run (demo/agent_trace_live_synthetic.json) under the current rules.

Offline, zero units, zero LLM spend. It rebuilds the two recorded check-ins from the final
verdicts' baseline/latest scores, runs the real compute_verdicts + claims.compare + decide,
and writes `lesson` and `decision_under_current_rules` at the top level. Recorded events are
never modified.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from skinproof import claims
from skinproof.agent import decide
from skinproof.verdict import compute_verdicts

P = Path(__file__).resolve().parents[1] / "demo" / "agent_trace_live_synthetic.json"
d = json.loads(P.read_text())
fin = d["events"][-1]["data"]
weeks = fin["weeks_elapsed"]
end = date.fromisoformat(d["date"])
start = end - timedelta(weeks=weeks)
old = [v for v in fin["verdicts"]]
checkins = [
    {"id": "base", "date": start.isoformat(), "scores": {v["concern"]: v["baseline"] for v in old}},
    {"id": "new", "date": end.isoformat(), "scores": {v["concern"]: v["latest"] for v in old}},
]
prod = {"start_date": start.isoformat(), "concerns": [v["concern"] for v in old if v["targeted"]]}
new_v = compute_verdicts(checkins, {v["concern"]: v["threshold"] for v in old}, prod, today=end)
checks = claims.compare([{k: c[k] for k in ("concern", "quote", "timeframe_weeks")} for c in fin["claims"]],
                        new_v["concerns"], weeks)
dec = decide(new_v["concerns"], checks, weeks)
pore = next(v for v in new_v["concerns"] if v["concern"] == "pore")
d["lesson"] = (
    "We caught our own false alarm. This run stopped on a single pore reading of "
    f"{pore['delta']:+.1f} points against a {pore['threshold']:.2f}-point noise threshold that came from only 3 "
    "synthetic variants (brightness and zoom, no rotation). The skin had not changed: the second photo was "
    "the same AI-generated face turned 3 degrees. Under the current confirmation rule a change beyond noise "
    "on one check-in is only LIKELY_WORSE and the decision is WAIT to re-check, not STOP. "
    "Calibration now also needs 4+ photos (5 to 6 recommended, with slight head turns).")
d["decision_under_current_rules"] = {
    "decision": dec["decision"], "wait_weeks": dec["wait_weeks"], "rule": dec["rule"],
    "headline": dec["headline"], "reasons": dec["reasons"],
    "pore_verdict": pore["verdict"], "claim_statuses": {c["concern"]: c["status"] for c in checks}}
P.write_text(json.dumps(d, indent=1))
print(json.dumps(d["decision_under_current_rules"], indent=1))
