# Lighting normalisation experiment (pre-registered)

Question: does YouCam Photo Lighting, applied before skin analysis, reduce the
same-day measurement noise of each concern?

Data: a folder of same-day selfies of ONE person (skin unchanged between photos),
taken with varied light, distance and angle. Never use photos of other people.

For every photo we analyse the raw image and the Photo Lighting output, then
compute, per concern, the range and SD across photos (raw vs lit).

Pre-registered adoption rule (fixed before any data is seen):

> Lighting normalisation is adopted only if it reduces the MEDIAN per-concern
> range by at least 25% AND does not change any concern's mean by more than 5
> points. Otherwise it stays off.

Run: `uv run python scripts/lighting_experiment.py PHOTO_DIR` prints the
estimated units; add `--yes-spend` to run. Results: `bench/lighting_RESULTS.md`.
