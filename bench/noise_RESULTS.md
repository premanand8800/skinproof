# Measurement noise on a real face

Subject: a volunteer, with consent. Scores only, no images. Raw scores (`raw_score`) from YouCam AI Skin Analysis, 8 concerns, 2026-11-01 evening, same room.

## A. Two selfies, 10 seconds apart, same light

Nothing about the skin changed between the two photos. Raw scores of the uncropped originals (YouCam accepted them as-is) and of the same photos after SkinProof's auto face-crop (the bytes the agent run used):

| Concern | Uncropped before | Uncropped after | Change | Cropped before | Cropped after | Change |
|---|---|---|---|---|---|---|
| Acne | 95.9 | 86.5 | -9.4 | 80.9 | 90.9 | +9.9 |
| Pores | 51.9 | 45.9 | -6.0 | 53.1 | 44.9 | -8.3 |
| Redness | 98.0 | 95.4 | -2.6 | 98.0 | 95.7 | -2.2 |
| Wrinkles | 88.0 | 90.5 | +2.5 | 87.5 | 90.5 | +3.1 |
| Age spots | 93.1 | 94.3 | +1.1 | 93.2 | 94.3 | +1.1 |
| Texture | 88.5 | 88.2 | -0.3 | 88.2 | 88.0 | -0.2 |
| Dark circles | 80.8 | 81.0 | +0.1 | 81.1 | 81.2 | +0.1 |
| Moisture | 53.9 | 53.9 | -0.1 | 53.9 | 53.1 | -0.8 |

Observations: acne moved about 10 points and pores 6 to 8 points within 10 seconds. For acne the direction reversed between the uncropped and the cropped version of the same two photos. Other concerns moved by less than 3.5 points.

## B. Six frontal photos, same evening

Six same-evening photos (WhatsApp, 1200x1600) were auto-cropped to the face and analysed. Two further photos (head turned) were rejected by YouCam's own gate with `error_large_face_angle`, no units charged. Before cropping, YouCam rejected most photos with `error_src_face_too_small`.

| Concern | WA0035 | WA0036 | WA0037 | WA0040 | WA0041 | WA0042 | Range | SD | SkinProof threshold |
|---|---|---|---|---|---|---|---|---|---|
| Pores | 61.3 | 63.8 | 63.5 | 35.9 | 58.4 | 60.0 | 27.9 | 10.6 | 27.9 |
| Moisture | 61.0 | 61.4 | 60.3 | 52.8 | 56.6 | 56.9 | 8.6 | 3.3 | 8.6 |
| Redness | 92.9 | 98.6 | 94.3 | 97.7 | 97.7 | 100.0 | 7.1 | 2.7 | 7.1 |
| Acne | 95.9 | 100.0 | 95.9 | 93.8 | 95.7 | 94.0 | 6.2 | 2.2 | 6.2 |
| Texture | 93.3 | 89.9 | 91.4 | 87.3 | 89.6 | 89.0 | 6.0 | 2.1 | 6.0 |
| Dark circles | 91.1 | 89.6 | 90.6 | 90.6 | 90.0 | 85.8 | 5.3 | 2.0 | 5.3 |
| Wrinkles | 96.3 | 96.4 | 95.7 | 91.2 | 94.7 | 95.3 | 5.2 | 1.9 | 5.2 |
| Age spots | 92.7 | 94.0 | 92.7 | 92.0 | 92.5 | 90.5 | 3.5 | 1.1 | 3.5 |

Thresholds come from `noise.estimate_noise` (`max(range, 2 x SD, 3)`). Here the range is the largest term in every row.

The pore outlier: WA0040 read 35.9 while the other five read 58.4 to 63.8. That one photo accounts for the whole 27.9-point pore range. Without it the pore range would be 5.4. A single odd photo can swing a score by about 25 points, which is why SkinProof keeps the observed range (not just the SD) as the threshold, calls a single beyond-noise reading only "likely", and needs a second one to confirm. The cost is that the pore threshold on this face (27.9) is so wide that pores can hardly ever be "proven" until more photos are added.

## Related

- Photo Lighting did not reduce noise enough to switch on: median range down 22%, below the pre-registered 25% (`bench/lighting_RESULTS.md`).
- The live agent run on this volunteer (calibrate on B, baseline check-in and new photo from A, cropped): decision WAIT 1 to 2 weeks, nothing proven. Replay: `demo/agent_trace_live_volunteer.json`.
- Limits: one person, one evening, six photos. Not a medical measurement.
