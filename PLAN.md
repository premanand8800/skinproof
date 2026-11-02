# SkinProof Agent: build plan

Deadline: Mon Nov 2 2026, 22:30 Nepal (11:45 ET). Target submit: 20:00 Nepal.

## Product

"Should I keep buying this product?" A user gives SkinProof a check-in selfie and the product they are testing (name, pasted claim text, product URL, or a photo of the label). An agent, driven by Gemini 3.8 Flash on OpenRouter, decides which YouCam APIs to call, measures the skin, compares each change with the noise measured on the user's own face, reads the product's claims, and ends with one decision: KEEP, STOP, or WAIT N WEEKS, with every step shown.

Rules that never bend:
- Only `raw_score` is used, never `ui_score`.
- A change counts only when it is larger than the measured noise for that concern.
- No overall progress score.
- Simulated images are always labelled "Simulation, not your result".
- A hard unit budget per agent run (default 40 YouCam units), enforced in code, not in the prompt.
- Face photos stay in `private/` and are never committed, except the consenting volunteer's demo photos in `demo/volunteer/`.

## YouCam APIs used

| API | Why |
|---|---|
| AI Skin Analysis | the measurement (raw scores, masks) |
| Skin Tone / Facial Color Tones Analyzer | context for redness and spot readings |
| Fitzpatrick Skin Type | context for the advice, never a verdict |
| Face Attributes & Ratio Analyzer | photo consistency: pose and framing check between check-ins |
| Skin Simulation | shows what the product's claimed improvement would look like (labelled simulation) |
| Photo Lighting | experiment: does normalising light before analysis reduce score noise? |
| Text-to-Image V2 | synthetic test face for smoke tests only, so no real person's photo is used in testing |

## Work split (parallel Sonnet agents, separate files)

| Agent | Owns | Delivers |
|---|---|---|
| A: agent core | `agent.py`, `budget.py`, agent endpoints in `web.py`, `demo/agent_trace.json`, tests | tool-calling loop, budget guard, SSE trace stream, demo replay |
| B: YouCam extras | `youcam.py` (generic task runner), `youcam_extra.py`, `scripts/smoke_extra.py`, tests | skin tone, Fitzpatrick, face attributes, skin simulation, photo lighting, synthetic test face |
| C: claims | `llm.py`, `claims.py`, tests | OpenRouter client; claim extraction from text/URL/label photo; claim-vs-measurement comparison |
| D: design | `static/index.html` | full redesign, agent screen with live step replay, demo mode |

Later (after selfies): noise calibration run, lighting experiment, live end-to-end run, deploy to skinproof.aether-soft.tech, README, demo video, submission.

## Shared interfaces

`llm.chat(messages, tools=None, model=None, max_tokens=4000, temperature=0.2, response_format=None) -> {"message": <OpenAI-style message dict>, "usage": {...}, "cost": float, "model": str}`. Default model `google/gemini-3.8-flash`. Images are passed as OpenAI `image_url` parts with base64 data URLs.

`youcam_extra` functions take image bytes and a `YouCamClient`, return plain dicts, cache by content hash, and report `units` spent:
- `skin_tone(client, img) -> {"tone": ..., "raw": ..., "units": n}`
- `fitzpatrick(client, img) -> {"type": "I".."VI", "raw": ..., "units": n}`
- `face_attributes(client, img) -> {"yaw":..,"pitch":..,"roll":..,"face_ratio":.., "raw":..., "units": n}` (fields as available)
- `skin_simulation(client, img, targets: dict[concern, strength]) -> {"image": bytes, "raw": ..., "units": n}`
- `photo_lighting(client, img) -> {"image": bytes, "units": n}`
- `estimated_cost(feature) -> float`

`claims.extract_claims(text=None, url=None, image=None) -> {"product": str, "claims": [{"concern": <SD action name or null>, "direction": "improve", "timeframe_weeks": int|None, "quote": str}], "source": "text|url|image"}`
`claims.compare(claims, verdicts) -> [{"concern", "quote", "timeframe_weeks", "weeks_elapsed", "measured": verdict dict or None, "status": "SUPPORTED"|"NOT_YET_SHOWN"|"CONTRADICTED"|"TOO_EARLY"|"NOT_MEASURABLE"}]`

Agent trace events (SSE, `data: <json>`): `{"type": "start"|"thought"|"tool_call"|"tool_result"|"final"|"error", "step": int, "tool": str?, "args": {}?, "summary": str, "units_spent": float, "units_left_in_budget": float, "data": {}?}`. The final event's `data` is `{"decision": "KEEP"|"STOP"|"WAIT", "wait_weeks": int?, "headline": str, "reasons": [str], "verdicts": [...], "claims": [...], "simulation_image": url?}`.
