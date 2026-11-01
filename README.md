# SkinProof

Demo video: _(placeholder)_
Live: https://skinproof.aether-soft.tech

**SkinProof is an agent that tells you whether to keep buying a skincare product, and only believes a change in your skin when it beats the noise measured on your own face, twice in a row.**

## The problem

People judge skincare in the bathroom mirror, under a different light every day. Skin-analysis scores move with light, angle and distance even when the skin has not changed, and most apps turn that wobble into a "progress score". People then rebuy a serum because a number rose three points under a kinder lamp. SkinProof never outputs an overall score. It answers one question, KEEP, STOP or WAIT, and shows every step it took.

## What SkinProof does

1. **Calibrate.** You take 4 to 6 photos of your face on one day (5 to 6 with slight head turns is best). Your skin is identical in all of them, so the spread in each concern's `raw_score` is your measurement noise. Threshold per concern = `max(range, 2 x SD, 3 points)`.
2. **Tell it the product.** Name plus the claims, as pasted text, a product URL, or a photo of the label.
3. **Check in** with a selfie (a face guide helps you frame it the same way).
4. **Ask the agent.** An LLM agent decides which YouCam tools to call, measures, compares each change with your noise, reads the product's claims, checks them against the measurements, and ends with KEEP, STOP or WAIT N weeks. The UI streams each step and its unit cost.

## YouCam APIs and why

Real unit costs charged by the API:

| API | Endpoint | Why | Units |
|---|---|---|---|
| AI Skin Analysis | `/s2s/v2.0/task/skin-analysis` | The measurement. Only `raw_score` is used, never `ui_score`. 8 concerns. | 9 (1-4 concerns) / 12 (5-8) |
| Face Attributes | `/s2s/v2.0/task/face-attr-analysis` | Photo-quality gate before measuring: face found, frontal, head angle, light, framing. Recommends a retake instead of measuring a bad photo. | 10 |
| Skin Tone | `/s2s/v2.0/task/skin-tone-analysis` | Context for redness and spot readings. Never a verdict. | 20 |
| Fitzpatrick | `/s2s/v2.0/task/fitzpatrick-scale-analyzer` | Context for the advice. Never a verdict. | 10 |
| Skin Simulation | `/s2s/v2.0/task/skin-simulation` | Shows what the product's claimed improvement would look like. Always labelled "Simulation, not your result". | 4 |
| Photo Lighting | `/s2s/v2.0/task/lighting` | Experiment: does normalising light before analysis reduce score noise? (`scripts/lighting_experiment.py`, `bench/lighting_README.md`) | 2 |
| Text-to-Image V2 | `/s2s/v2.0/task/text-to-image/youcam` | Generates a synthetic test face, so no real person's photo was used in testing. | 1 |

## The agent

- Model: Gemini 3.8 Flash on OpenRouter, driving a tool loop (`get_context`, `check_photo_consistency`, `analyze_skin`, `skin_context`, `compare_with_noise`, `read_product_claims`, `check_claims`, `simulate_claimed_result`, `finish`).
- **Budget enforced in code, not in the prompt:** 40 YouCam units and $0.10 of LLM spend per run, checked before every paid call, plus a 12-step cap. Repeated calls are cached and free.
- **Deterministic rule next to the model.** `decide()` in `src/skinproof/agent.py` computes KEEP/STOP/WAIT from the verdicts alone. If the model disagrees, both are shown and the model is not silently overridden. If the model never calls `finish`, the rule decides and the UI says so.
- Refuses to finish before measuring, and blocks measuring when the photo gate says retake.

## The confirmation rule, and the false alarm we caught

A change beyond noise on **one** check-in is only **Likely improved / Likely worse, needs confirmation**. It becomes **Improved / Worse (proven)** only when the two most recent counted check-ins are both beyond the threshold in the same direction versus the baseline (median of the first two check-ins when 4+ exist, otherwise the first). The agent may say STOP or KEEP only on proven results. A likely result gives WAIT 2 weeks, "re-check to confirm", naming the concern. Eight concerns are tested at once, so some will cross the line by chance; every verdict output says how many were tested.

Why: in our first live end-to-end run on an AI-generated face, the second photo was the same face turned **3 degrees**. The skin had not changed, yet pores moved **4.6 points** against a 4.03 threshold calibrated from only 3 synthetic variants (brightness and zoom, no rotation). The agent said STOP. That was a false alarm, and it is our most honest finding: a 3 degree head turn moved a score by 4.6 points. We fixed the method instead of hiding it. The original run is kept untouched in `demo/agent_trace_live_synthetic.json`; under today's rules the same inputs give **WAIT 2 weeks (likely worse, re-check)** (rule R2b). The app replays that run with a banner explaining it ("The false alarm we caught" on the sample screen, or `#run=live-synthetic`). Calibration now requires 4+ photos; with fewer than 6 the observed range underestimates noise, so 5 to 6 with head turns is recommended. The synthetic perturbations include rotation of plus and minus 3 degrees and a small yaw-like crop shift.

## Reading the product's claims

`extract_claims` takes pasted text, a URL or a label photo and returns measurable claims (concern, quote, timeframe). `compare` is deterministic and gives each claim a status:

- `SUPPORTED`: proven improvement
- `CONTRADICTED`: proven worsening
- `NEEDS_CONFIRMATION`: one check-in beyond noise (likely)
- `NOT_YET_SHOWN`: no proven change past the claimed time
- `TOO_EARLY`: the claimed timeframe has not passed
- `NOT_MEASURABLE`: a photo cannot measure it (scent, "dermatologist tested")

## Privacy

Face photos are stored locally under `private/`, which is gitignored, and are never committed. Tests and the demo use invented scores or an AI-generated face. The YouCam and OpenRouter keys live only on the server (`.env`, gitignored).

## Honest limitations

- A photo score is a proxy, not a dermatological measurement. Not a medical device.
- Calibration from a handful of photos is still an estimate. Proven means "beyond your noise twice in a row", not "caused by the product": anything else you changed in the same weeks is invisible to us.
- Many concerns are tested together, so false positives remain possible; confirmation reduces them but does not remove them.
- Pose, framing and light still matter. The photo gate catches obvious problems only.
- The live validation used one synthetic face. The sample demo uses invented data and is labelled so.
- Simulations are illustrations of a claim, never evidence.

## Run it

```
uv sync
# create .env with your YouCam and OpenRouter keys (never committed)
uv run skinproof serve --port 8100
uv run pytest
```

Without keys, "Try the sample" replays a recorded run on invented data. Offline scripts: `scripts/make_demo_trace.py` (regenerates the sample trace), `scripts/reevaluate_recorded_run.py` (re-evaluates the live run under current rules). `scripts/e2e_live.py` and `scripts/synthetic_noise.py` spend units and need an explicit flag.

Tests: 123 passing (`uv run pytest`).

## Built during the hackathon

Local time, UTC+5:45, from `git log`:

| Date, time | Commit |
|---|---|
| 2026-11-01 19:06 | SkinProof core: YouCam client, noise calibration, verdicts, store, web UI, tests |
| 2026-11-01 19:07 | README, LICENSE, opt-in synthetic noise script |
| 2026-11-01 22:07 | Build plan for the SkinProof agent |
| 2026-11-01 22:11 | OpenRouter client and claim extraction and comparison |
| 2026-11-01 22:14 | Tool-calling agent, unit and LLM budget, SSE endpoints, sample trace |
| 2026-11-01 22:14 | Redesigned UI: live agent investigation screen |
| 2026-11-01 22:14 | Extra YouCam endpoints (skin tone, Fitzpatrick, face attributes, simulation, lighting, text-to-image) |
| 2026-11-01 22:16 | Photo consistency from real Face Attributes fields, real unit costs |
| 2026-11-01 22:23 | Stored-run replay, live E2E script, synthetic trace, lighting experiment |
| 2026-11-01 | Confirmation rule, 4-photo calibration, photo upload for the agent, README |
