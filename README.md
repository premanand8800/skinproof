# SkinProof

**No fake progress. Just proof.**

SkinProof answers one question while you stand at the mirror: *is this skincare product actually working on my face?* It runs your weekly selfies through the YouCam AI Skin Analysis API and only calls a change real when it is bigger than the measurement noise on your own face.

## The problem

Skin-analysis scores move with lighting, camera angle and distance even when your skin has not changed. Most apps show a single score that goes up and down and call it progress. People then rebuy a serum because a number rose three points under a kinder bathroom light. SkinProof refuses to do that. It never outputs a single overall progress score.

## How the noise-gated verdict works

1. **Calibrate.** You take 3 to 6 photos of the same face on the same day, changing light, angle and distance. The skin is identical, so any spread in a concern's `raw_score` is measurement noise.
2. **Proof threshold per concern** = `max(observed range, 2 x sample SD, 3 points)`. The 3-point floor stops a lucky set of near-identical retakes from making us overconfident.
3. **Check in** weekly. For each concern, `delta = later - baseline` (higher raw score is healthier).
   - `delta > threshold`: **Improved**
   - `delta < -threshold`: **Worse**
   - otherwise: **No proven change yet** (not the same as "no effect")
4. With 4 or more counted check-ins we compare the median of the first two with the median of the last two. With fewer, first versus latest.
5. **Quality gate.** If YouCam rejects a photo (face too small, too dark, out of frame, too small an image) the check-in is not counted, and you are told how to retake it. At least 2 counted check-ins are needed.
6. **What to do next** is shown per concern, e.g. no proven change after 8 weeks on a product that claims that concern: "don't rebuy yet".

We use `raw_score`, not `ui_score`. Perfect Corp's own docs say `ui_score` is adjusted upward to be more flattering, which is the opposite of what a proof tool needs.

`src/skinproof/noise.py` also has a synthetic perturbation generator (brightness +-15%, zoom 90-100%, JPEG q70, 3 degree rotation) for a cheaper lower bound. It never runs automatically. `scripts/synthetic_noise.py --yes-spend` runs it.

## YouCam APIs used

| Endpoint | Use |
|---|---|
| `POST /s2s/v2.0/file` + pre-signed `PUT` | register and upload each (preprocessed) selfie |
| `POST /s2s/v2.0/task/skin-analysis` | SD actions: wrinkle, pore, texture, acne, redness, age_spot, dark_circle_v2, moisture (12 units); `enable_mask_overlay` for before/after overlays; `format: json` |
| `GET /s2s/v2.0/task/skin-analysis/{id}` | poll every 2 s, 90 s timeout; parse `ui_score`, `raw_score`, `mask_urls`, plus any overall / skin age |
| `GET /s2s/v1.0/client/credit` | units remaining shown in the UI |
| `GET /s2s/v2.0/credit/feature-cost` | cost table (`YouCamClient.feature_cost()`) |

Units are precious, so every analysis is cached on disk (`.cache/youcam/`) keyed by the SHA-256 of the preprocessed image plus the action set. The same photo is never paid for twice. Photos are auto-oriented from EXIF, converted to JPEG, long side capped at 2560 px, short side floored at 480 px. SD and HD actions are never mixed. The API key stays on the server; the browser never sees it.

## Run it

```
uv sync
echo "YOUCAM_API_KEY=your_key" > .env
uv run skinproof serve --port 8100
uv run pytest            # no network, no units
```

Open http://localhost:8100. Click "See a sample" for a full walkthrough with invented data and no photo.

## Privacy

Photos are stored only under `private/` on your machine, which is gitignored along with `.env` and `.cache/`. The only place a photo leaves your machine is the upload to YouCam for analysis. There is no account system and no telemetry.

## Honest limitations

- A skin-analysis model is not a dermatologist. SkinProof is a measurement tool, not medical advice.
- Noise is estimated from a handful of photos. More retakes give a steadier threshold.
- "No proven change" means we cannot tell, not that the product does nothing. Subtle effects smaller than your photo noise are invisible to any selfie-based method.
- Real skin changes with season, sleep, cycle and sun, which SkinProof does not control for. Keep your routine otherwise steady.
- Mask overlays come from YouCam URLs that expire, so SkinProof saves a local copy at check-in time.
- The demo uses invented scores and is labelled as sample data in the UI.
- The end-to-end YouCam flow is unit-tested against mocks. A live run needs your own selfies.

## Built during the hackathon

Built for the Perfect Corp YouCam API Skin AI and eCommerce VTO Hackathon, Nov 2026. All code in this repository was written during the hackathon period.
