# SkinProof: Devpost submission text

## Tagline (under 200 characters)

An agent that tells you KEEP, STOP or WAIT on a skincare product, and only believes a change in your skin when it beats the noise measured on your own face, twice in a row.

## Elevator pitch

Skin-analysis scores move with light, angle and distance even when the skin has not changed. We measured it: two selfies of the same volunteer 10 seconds apart moved acne by almost 10 points. Most apps still turn that wobble into a "progress score", and people rebuy a serum because a number rose under a kinder lamp. SkinProof first measures the noise on your own face with YouCam AI Skin Analysis, then runs an agent that checks each change against that noise, reads the product's claims, and ends with KEEP, STOP or WAIT N weeks. It shows every step and its cost, and it says "nothing proven" when that is the truth.

## Inspiration

People judge skincare in the bathroom mirror under a different light every day. Surveys show shoppers do not trust the claims (86% of 3,224 US women believed some brands exaggerate; The Benchmarking Company, 2024), and retailers and brands are required to keep performance claims within the supporting evidence (EU Regulation 655/2013). Dermatology guidance says even small changes in capture conditions change photos drastically (Nair and Santhanam, 2016), and trials of ingredients run for weeks (a 12-week niacinamide trial with images every 4 weeks).

Yet the consumer tool for "is this working?" is a single score from a single selfie. We wanted to build the opposite: an honest referee. Sources and what we could not verify are in [docs/IMPACT.md](IMPACT.md).

## What it does

1. **Calibrate.** You take 4 to 6 photos of your face on one day. Your skin is identical in all of them, so the spread in each concern's `raw_score` is your measurement noise. Threshold per concern = max(range, 2 x SD, 3 points).
2. **Tell it the product.** Name plus claims, as pasted text, a product URL, or a photo of the label.
3. **Check in** with a selfie (a face guide helps framing).
4. **Ask the agent.** It decides which YouCam tools to call, measures, compares each change with your noise, reads the claims, checks them against the measurements, and ends with KEEP, STOP or WAIT N weeks. The UI streams each step with its unit cost.

It never outputs an overall score. A change beyond noise on one check-in is only "likely, needs confirmation". It is "proven" only when the two most recent check-ins are both beyond the threshold in the same direction. Each product claim gets one of SUPPORTED, CONTRADICTED, NEEDS_CONFIRMATION, NOT_YET_SHOWN, TOO_EARLY or NOT_MEASURABLE (a photo cannot measure scent or "dermatologist tested").

**What we measured on a real face** (a volunteer, with consent; scores only, no images are published):
- Two selfies 10 seconds apart, same room, same light: acne moved 9.4 points (uncropped) or 9.9 (cropped, opposite direction), pores 6.0 to 8.3.
- Six same-evening frontal photos: pore readings 61.3, 63.8, 63.5, 35.9, 58.4, 60.0, a 27.9-point range. One odd photo accounts for all of it (without it the range is 5.4).
- The live agent run on this face ends **WAIT, nothing proven**.
- Photo Lighting reduced the median range by 22%, below our pre-registered 25% bar, so it is off.

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
| Photo Lighting | `/s2s/v2.0/task/lighting` | An experiment: does normalising light reduce score noise? (`scripts/lighting_experiment.py`) Result: no, not enough. | 2 |
| Text-to-Image V2 | `/s2s/v2.0/task/text-to-image/youcam` | Generates a synthetic test face so no real person's photo was needed for early testing. | 1 |

The non-obvious part is using the skin analyser as a **noisy instrument to be characterised**, not an oracle: we use it on your own face to learn its error bars, then judge the product against those.

**The agent.** Gemini 3.8 Flash on OpenRouter drives a tool-calling loop (`get_context`, `check_photo_consistency`, `analyze_skin`, `skin_context`, `compare_with_noise`, `read_product_claims`, `check_claims`, `simulate_claimed_result`, `finish`).
- **Budget enforced in code, not in the prompt:** 40 YouCam units and $0.10 of LLM spend per run, checked before every paid call, plus a 12-step cap. Repeated calls are cached and free.
- **A deterministic rule next to the model.** `decide()` computes KEEP/STOP/WAIT from the verdicts alone. If the model disagrees, both are shown and the model is not silently overridden. If the model never calls `finish`, the rule decides and the UI says so.
- **Confirmation rule** as above, implemented in code and covered by tests.
- Claim reading from pasted text, a URL, or a label photo.
- **Auto face-crop** before YouCam (`src/skinproof/faceprep.py`).
- **Spend guard** with daily caps on units and dollars, global and per IP, so a public demo cannot drain the keys.
- 140 automated tests (`uv run pytest`).

Python backend, a single-page UI that streams the agent's steps over server-sent events, a recorded-trace replay mode, and a public deployment.

**Built during the submission period.** The solo builder (Nepal, UTC+5:45) wrote it in the hackathon window; every commit in the repository is dated 1 to 2 November 2026 (`git log`, first commit 2026-11-01 19:06 local).

## Challenges we ran into

- **The false alarm.** Our first live end-to-end run, on an AI-generated face, used a second photo of the same face turned 3 degrees. The skin had not changed, but pores moved 4.6 points against a 4.03 threshold calibrated from only 3 variants, and the agent said STOP. We fixed the method rather than hiding it: calibration now needs 4+ photos, 5 to 6 with head turns recommended, a single beyond-noise reading is only "likely", and a second confirms. The original run is kept untouched in `demo/agent_trace_live_synthetic.json`; under today's rules it gives WAIT 2 weeks.
- **YouCam rejects small faces and turned heads.** Phone selfies were mostly rejected with `error_src_face_too_small`, so we added a face auto-crop. Two turned-head photos were rejected with `error_large_face_angle` (no units charged), which we report instead of working around.
- **Face Attributes has no head angles** in the fields we could use, so our photo-consistency check is built from what it does return (frontal, light, framing) and cannot catch every pose problem.
- **`ui_score` vs `raw_score`.** `ui_score` is a display score; we use only `raw_score` and say so.
- **Not fooling ourselves.** A pore reading that jumped 25 points from one photo forced us to keep the full observed range as the threshold, which makes pores nearly impossible to "prove" on that face. We left it that way and say so.

## Accomplishments that we're proud of

- A concrete, measured finding about the instrument: acne about 10 points and pores 6 to 8 points apart within 10 seconds, and a 27.9-point pore range across six same-evening photos.
- Catching our own false alarm, publishing it, and fixing the method.
- A pre-registered experiment that came out negative (lighting, 22% < 25%) and was reported as negative.
- An agent whose budget and decision rule live in code, whose every step is visible, and that is allowed to answer "WAIT, nothing proven".
- Consent-based real-face data, with only scores published.

## What we learned

- A skin score is a noisy reading. Learning the noise first changes what an honest product looks like.
- Rotation of just 3 degrees can move a score by more than a calibration based on brightness and zoom alone.
- "Normalise the lighting" sounds like a fix and measured as a partial one.
- Letting a language model call the tools but not the verdict makes behaviour much more predictable.
- Limits matter: this is one person, one evening. We would rather say that than overclaim.

## What's next

See [docs/IMPACT.md](IMPACT.md). In short:
1. An 8-week pilot with 10 to 20 shoppers on products they already use; publish the results either way.
2. A consistent-capture guide.
3. A retailer "verified results" badge: a brand lists a claim and timeframe, opted-in customers are measured with the same rule, and only aggregate counts are shown.
4. A clinic workflow: per-patient noise estimate so before/after photos are flagged only when the difference exceeds it.
5. Comparing YouCam raw scores with a clinical imaging reference.

**Not proven yet:** no longitudinal user study; one volunteer; "proven" means beyond your noise twice in a row, not caused by the product; this is not a medical device.

## Built with

Python, FastAPI, Server-Sent Events, OpenRouter, Gemini 3.8 Flash, tool calling, OpenCV (face crop), YouCam AI Skin Analysis API, YouCam Face Attributes API, YouCam Skin Tone API, YouCam Fitzpatrick API, YouCam Skin Simulation API, YouCam Photo Lighting API, YouCam Text-to-Image V2 API, pytest, uv, HTML/JavaScript.

## Links

- Live demo: https://skinproof.aether-soft.tech
- Repo: https://github.com/premanand8800/skinproof
- Demo video: _(add link)_

## Testing instructions for judges

No sign-up and no key needed for the replays.

1. Open https://skinproof.aether-soft.tech and click **Try the sample**: a recorded run on invented, labelled data.
2. https://skinproof.aether-soft.tech/#run=live-volunteer : a real agent run on a consenting volunteer's face. Ends **WAIT, nothing proven**, with every YouCam call and its cost.
3. https://skinproof.aether-soft.tech/#run=live-synthetic : the false alarm we caught, with a banner explaining it (an AI-generated face, original trace untouched).
4. **Live analysis** (your own photos) works but is capped per day by the spend guard. If the cap is reached the site says so and the three replays still work. Photos are stored on the server under a gitignored directory and never committed.
5. To run locally: `uv sync`, copy `.env.example` to `.env`, add your keys, `uv run skinproof serve --port 8100`, `uv run pytest`.

Note: the volunteer consented to the use of their scores; no images of them are published.
