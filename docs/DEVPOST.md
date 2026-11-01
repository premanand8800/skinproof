# SkinProof: Devpost submission text

## Tagline (under 200 characters)

SkinProof makes YouCam's skin measurements trustworthy enough to act on: it measures the noise on your own face, aligns your captures, and says KEEP, STOP or WAIT only when a change beats that noise twice in a row.

## Elevator pitch

Every camera-based measurement varies with light, angle and framing, and skin analysis is no exception. We measured it with YouCam AI Skin Analysis: two selfies of the same volunteer 10 seconds apart moved acne by almost 10 points, and across six same-evening photos one pore reading was a 27.9-point outlier. YouCam's own quality gates (face too small, face angle too large) reject unusable photos up front; SkinProof adds what a buy-or-stop decision needs on top. It measures the noise on your own face, helps you capture the same way each time with Mirror Mode (a ghost overlay of your last photo plus live light, evenness, sharpness and alignment checks), and applies a confirmation rule so a change counts only when it beats your noise twice in a row. A tool-calling agent then reads the product's claims and ends with KEEP, STOP or WAIT N weeks. It shows every step and its cost, and says "nothing proven" when that is the truth. Built on seven YouCam APIs plus the YouCam MCP server.

## Inspiration

People judge skincare in the bathroom mirror under a different light every day. Surveys show shoppers do not trust the claims (86% of 3,224 US women believed some brands exaggerate; The Benchmarking Company, 2024), and retailers and brands are required to keep performance claims within the supporting evidence (EU Regulation 655/2013). Dermatology guidance says even small changes in capture conditions change photos drastically (Nair and Santhanam, 2016), and trials of ingredients run for weeks (a 12-week niacinamide trial with images every 4 weeks).

YouCam gives a precise reading of each photo. What a shopper needs next is to know how far that reading moves between two photos of the same skin, so a score can drive a decision. We built that layer. Sources and what we could not verify are in [docs/IMPACT.md](IMPACT.md).

## What it does

1. **Calibrate.** You take 4 to 6 photos of your face on one day, or the **60-second quick noise check** (4 guided poses). Your skin is identical in all of them, so the spread in each concern's `raw_score` is your measurement noise. Threshold per concern = max(range, 2 x SD, 3 points). Only `raw_score` is used: it is the right signal for measurement, while `ui_score` is designed for display.
2. **Capture with Mirror Mode.** In-browser camera with a ghost overlay of your last photo and on-device checks for light, evenness, sharpness and alignment. Nothing leaves the browser until you press Measure; capture metadata is stored with each photo.
3. **Before you buy** (`/buy`). Paste claims, a URL or a label photo: a claim audit, measurable vs marketing-only, the earliest week a change could be checked, a trial-duration reminder from published trials, and a prefill into the app. A retailer "Verified on real faces" badge is shown as a labelled concept.
4. **Ask the agent.** It decides which YouCam tools to call, measures, compares each change with your noise, reads the claims, checks them against the measurements, and ends with KEEP, STOP or WAIT N weeks. The UI streams each step with its unit cost.
5. **What YouCam saw.** Skin Analysis masks, a Skin Simulation (labelled "Simulation, not your result"), Skin Tone (#a27d61) and Fitzpatrick IV for the volunteer run. Context only, never a verdict.

It never outputs an overall score. A change beyond noise on one check-in is only "likely, needs confirmation". It is "proven" only when the two most recent check-ins are both beyond the threshold in the same direction. Each product claim gets one of SUPPORTED, CONTRADICTED, NEEDS_CONFIRMATION, NOT_YET_SHOWN, TOO_EARLY or NOT_MEASURABLE (a photo cannot measure scent or "dermatologist tested").

**What we measured on a real face** (a volunteer, with consent; scores only, no images are published). These are normal capture variations, which is why per-person noise matters:
- Two selfies 10 seconds apart, same room, same light: acne moved 9.4 points (uncropped) or 9.9 (cropped, opposite direction), pores 6.0 to 8.3.
- Six same-evening frontal photos: pore readings 61.3, 63.8, 63.5, 35.9, 58.4, 60.0, a 27.9-point range. One odd photo accounts for all of it (without it the range is 5.4).
- The live agent run on this face ends **WAIT, nothing proven**.
- We tested Photo Lighting as a variation reducer: it cut the median range by 22%, just under our pre-registered 25% bar, so we left it off.

Full tables: [bench/noise_RESULTS.md](../bench/noise_RESULTS.md), [bench/lighting_RESULTS.md](../bench/lighting_RESULTS.md).

## How we built it

**YouCam APIs used, and why**

| API | Endpoint | Why | Units |
|---|---|---|---|
| AI Skin Analysis | `/s2s/v2.0/task/skin-analysis` | The measurement. Only `raw_score` is used, never `ui_score`. 8 concerns. | 9 (1-4 concerns) / 12 (5-8) |
| Face Attributes | `/s2s/v2.0/task/face-attr-analysis` | Photo-quality gate before measuring: face found, frontal, head angle, light, framing. Recommends a retake instead of measuring a bad photo. | 10 |
| Skin Tone | `/s2s/v2.0/task/skin-tone-analysis` | Context for redness and spot readings, never a verdict. | 20 |
| Fitzpatrick | `/s2s/v2.0/task/fitzpatrick-scale-analyzer` | Context for the advice, never a verdict. | 10 |
| Skin Simulation | `/s2s/v2.0/task/skin-simulation` | Shows what the product's claimed improvement would look like, always labelled "Simulation, not your result". | 4 |
| Photo Lighting | `/s2s/v2.0/task/lighting` | An experiment: does normalising light reduce score variation? (`scripts/lighting_experiment.py`) Result: 22%, under our 25% bar. | 2 |
| Text-to-Image V2 | `/s2s/v2.0/task/text-to-image/youcam` | Generates a synthetic test face so no real person's photo was needed for early testing. | 1 |
| YouCam MCP server | `https://mcp-api-01.makeupar.com/mcp/beauty` | 50 beauty tools listed; Skin Analysis through MCP matched REST to within 0.001 on a synthetic face. | same as REST |

The non-obvious part is treating the skin analyser as a **measuring instrument with error bars to be characterised**: like any camera-based measurement it varies with light, angle and framing, so we use it on your own face to learn those error bars, then judge the product against them.

**The agent.** Gemini 3.8 Flash on OpenRouter drives a tool-calling loop (`get_context`, `check_photo_consistency`, `analyze_skin`, `skin_context`, `compare_with_noise`, `read_product_claims`, `check_claims`, `simulate_claimed_result`, `finish`).
- **Budget enforced in code, not in the prompt:** 40 YouCam units and $0.10 of LLM spend per run, checked before every paid call, plus a 12-step cap. Repeated calls are cached and free.
- **A deterministic rule next to the model.** `decide()` computes KEEP/STOP/WAIT from the verdicts alone. If the model disagrees, both are shown and the model is not silently overridden. If the model never calls `finish`, the rule decides and the UI says so.
- **Confirmation rule** as above, implemented in code and covered by tests.
- Claim reading from pasted text, a URL, or a label photo.
- **Auto face-crop** before YouCam (`src/skinproof/faceprep.py`).
- **Spend guard** with daily caps on units and dollars, global and per IP, so a public demo cannot drain the keys.
- **MCP in both directions.** SkinProof calls the YouCam MCP server (JSON-RPC client; `SKINPROOF_YOUCAM_TRANSPORT=mcp`) and is itself an MCP server (stdio) for Claude Desktop or Cursor.
- **Privacy.** Photos are stored on the server under a gitignored directory; a "Delete my data" button removes them; Mirror Mode checks run on-device.
- 155 automated tests (`uv run pytest -q`). About 386 of our 1040 starting YouCam units used so far (balance 654).

Python backend, a single-page UI that streams the agent's steps over server-sent events, a recorded-trace replay mode, and a public deployment.

**Built during the submission period.** The solo builder (Nepal, UTC+5:45) wrote it in the hackathon window; every commit in the repository is dated 1 to 2 November 2026 (`git log`, first commit 2026-11-01 19:06 local, latest 2026-11-02 00:27).

## Challenges we ran into

- **The false alarm.** Our first live end-to-end run, on an AI-generated face, used a second photo of the same face turned 3 degrees. The skin had not changed, but pores moved 4.6 points against a 4.03 threshold calibrated from only 3 variants, and the agent said STOP. We fixed the method rather than hiding it: calibration now needs 4+ photos, 5 to 6 with head turns recommended, a single beyond-noise reading is only "likely", and a second confirms. The original run is kept untouched in `demo/agent_trace_live_synthetic.json`; under today's rules it gives WAIT 2 weeks.
- **YouCam's quality gates, and fitting our capture to them.** Phone selfies were mostly rejected up front with `error_src_face_too_small`, so we added a face auto-crop. Two turned-head photos were rejected with `error_large_face_angle` (no units charged). These gates protect measurement quality; we report them instead of working around them, and Mirror Mode now helps users frame correctly in the first place.
- **Face Attributes has no head angles** in the fields we could use, so our photo-consistency check is built from what it does return (frontal, light, framing) and cannot catch every pose problem.
- **`ui_score` vs `raw_score`.** `ui_score` is designed for display; `raw_score` is the right signal for measurement, so we use only `raw_score` and say so.
- **Not fooling ourselves.** A pore reading that jumped 25 points from one photo forced us to keep the full observed range as the threshold, which makes pores nearly impossible to "prove" on that face. We left it that way and say so.

## Accomplishments that we're proud of

- A concrete, measured finding about capture variation: acne about 10 points and pores 6 to 8 points apart within 10 seconds, and a 27.9-point pore range across six same-evening photos.
- Catching our own false alarm, publishing it, and fixing the method.
- A pre-registered experiment that came out negative (lighting, 22% < 25%) and was reported as negative.
- An agent whose budget and decision rule live in code, whose every step is visible, and that is allowed to answer "WAIT, nothing proven".
- Consent-based real-face data, with only scores published.
- Mirror Mode: on-device capture alignment with a ghost of your last photo, plus a 60-second quick noise check.
- Using YouCam both ways over MCP, with identical results to 0.001.

## What we learned

- Any camera-based score varies with capture. Learning that variation first changes what an honest product looks like.
- Rotation of just 3 degrees can move a score by more than a calibration based on brightness and zoom alone.
- "Normalise the lighting" sounds like a fix and measured as a partial one.
- Letting a language model call the tools but not the verdict makes behaviour much more predictable.
- Limits matter: this is one person, one evening. We would rather say that than overclaim.

## What's next

See [docs/IMPACT.md](IMPACT.md). In short:
1. An 8-week pilot with 10 to 20 shoppers on products they already use; publish the results either way.
2. A consistent-capture guide.
3. A retailer "Verified on real faces" badge (shown today as a labelled concept in `/buy`): a brand lists a claim and timeframe, opted-in customers are measured with the same rule, and only aggregate counts are shown.
4. A clinic workflow: per-patient noise estimate so before/after photos are flagged only when the difference exceeds it.
5. Comparing YouCam raw scores with a clinical imaging reference.

**Not proven yet:** no longitudinal user study; one volunteer, one evening; Mirror Mode capture thresholds were tuned on a synthetic feed; "proven" means beyond your noise twice in a row, not caused by the product; this is not a medical device.

## Built with

Python, FastAPI, MCP (client and server), WebRTC camera (getUserMedia), Server-Sent Events, OpenRouter, Gemini 3.8 Flash, tool calling, OpenCV (face crop), YouCam AI Skin Analysis API, YouCam Face Attributes API, YouCam Skin Tone API, YouCam Fitzpatrick API, YouCam Skin Simulation API, YouCam Photo Lighting API, YouCam Text-to-Image V2 API, YouCam MCP server, pytest, uv, HTML/JavaScript.

## Links

- Live demo: https://skinproof.aether-soft.tech
- Repo: https://github.com/premanand8800/skinproof
- Demo video: _(add link before submitting)_

## Testing instructions for judges

No sign-up and no key needed for the replays.

1. **Live site:** https://skinproof.aether-soft.tech. Click **Try the sample**: a recorded run on invented, labelled data.
2. **Real-face replay:** https://skinproof.aether-soft.tech/#run=live-volunteer. A real agent run on a consenting volunteer's face. Ends **WAIT, nothing proven**, with every YouCam call and its cost, plus the "What YouCam saw" panel.
3. **False-alarm replay:** https://skinproof.aether-soft.tech/#run=live-synthetic. The false alarm we caught, with a banner explaining it (an AI-generated face, original trace untouched).
4. **Before you buy:** https://skinproof.aether-soft.tech/buy and click **Try an example** (no key, no units). Paste your own claims or a URL for a live check (10 per IP per day).
5. **Mirror Mode:** open the live site on a phone and start a check-in or the 60-second quick noise check. Allow the camera; the ghost overlay and the light, evenness, sharpness and alignment pills run on-device. The camera needs HTTPS, which the live site has.
6. **Live analysis** with your own photos works but is capped per day by the spend guard (120 shared units, 72 per IP, 3 agent runs per IP, $1 shared LLM spend). If a cap is reached the site says so and the replays still work. Use "Delete my data" to remove your photos.
7. **Repo:** https://github.com/premanand8800/skinproof. To run locally: `uv sync`, copy `.env.example` to `.env`, add your keys, `uv run skinproof serve --port 8100`, `uv run pytest -q` (155 tests).
8. **Demo video:** _(add link before submitting)_

Honest limitations: one volunteer, one evening; Mirror Mode capture thresholds were tuned on a synthetic feed; no longitudinal study yet. The volunteer consented to the use of their scores; no images of them are published.
