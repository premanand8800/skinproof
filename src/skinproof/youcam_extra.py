"""Extra YouCam endpoints: skin tone, Fitzpatrick, face attributes, skin simulation,
photo lighting, text-to-image V2.

All functions take image bytes and a YouCamClient, cache on disk by content hash
(a cache hit spends 0 units), and return plain dicts with a ``units`` field.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .youcam import YouCamClient, YouCamError, preprocess_image

# Unit costs from the official docs / GET /s2s/v2.0/credit/feature-cost.
COSTS: dict[str, float] = {
    "skin_tone": 20.0,
    "fitzpatrick": 10.0,
    "face_attributes": 10.0,   # 1-5 features; 6-14 = 20; 15+ = 30
    "skin_simulation": 4.0,    # 1-4 concerns; 5-10 = 6
    "photo_lighting": 2.0,
    "text_to_image": 1.0,
    "skin_analysis": 12.0,     # SD: 9 for 1-4 concerns, 12 for 5-8
}

PATHS = {
    "skin_tone": "/s2s/v2.0/task/skin-tone-analysis",
    "fitzpatrick": "/s2s/v2.0/task/fitzpatrick-scale-analyzer",
    "face_attributes": "/s2s/v2.0/task/face-attr-analysis",
    "skin_simulation": "/s2s/v2.0/task/skin-simulation",
    "photo_lighting": "/s2s/v2.0/task/lighting",
    "text_to_image": "/s2s/v2.0/task/text-to-image/youcam",
}

# Skin Simulation takes these concern keys, each 0.0-1.0.
SIM_CONCERNS = ["wrinkle", "radiance", "oiliness", "acne", "eye_bags", "dark_circle",
                "spots", "pores", "texture", "redness"]
# SkinProof (SD action name) -> simulation key
SIM_ALIASES = {"dark_circle_v2": "dark_circle", "eye_bag": "eye_bags", "age_spot": "spots",
               "pore": "pores", "wrinkles": "wrinkle"}

DEFAULT_FACE_FEATURES = ["faceShape", "faceAspectRatio", "horizontalThird", "verticalFifth", "age"]


def estimated_cost(feature: str, n: int | None = None) -> float:
    """Units a call would cost. ``n`` = number of concerns (simulation) or features (face_attributes)."""
    if feature == "skin_simulation" and n is not None:
        return 4.0 if n <= 4 else 6.0
    if feature == "skin_analysis" and n is not None:
        return 9.0 if n <= 4 else 12.0
    if feature == "face_attributes" and n is not None:
        return 10.0 if n <= 5 else 20.0 if n <= 14 else 30.0
    if feature not in COSTS:
        raise KeyError(f"unknown feature {feature!r}")
    return COSTS[feature]


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _cache_path(client: YouCamClient, feature: str, key: str) -> Path:
    return Path(client.cache_dir) / f"{feature}-{key}.json"


def _cached(client, feature, key, run: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    path = _cache_path(client, feature, key)
    if path.is_file():
        return json.loads(path.read_text()), True
    raw = run()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return raw, False


def _results(raw: dict[str, Any]) -> dict[str, Any]:
    return (raw.get("data") or {}).get("results") or {}


def _fetch_image(client: YouCamClient, raw: dict[str, Any], feature: str, key: str) -> bytes:
    """Result images expire after 2h, so keep the bytes next to the cached JSON."""
    img_path = Path(client.cache_dir) / f"{feature}-{key}.img"
    if img_path.is_file():
        return img_path.read_bytes()
    url = _results(raw).get("url")
    if not url:
        raise YouCamError("NoResultImage", "The service returned no result image.")
    data = client.download_bytes(url)
    if not data:
        raise YouCamError("DownloadFailed", "Could not download the result image.")
    img_path.parent.mkdir(parents=True, exist_ok=True)
    img_path.write_bytes(data)
    return data


def _prep(img: bytes) -> bytes:
    try:
        return preprocess_image(img)
    except Exception as e:  # unreadable image
        raise YouCamError("error_decode_image", "That file is not a readable image.") from e


# -- analyzers -------------------------------------------------------------
def skin_tone(client: YouCamClient, img: bytes, *, strictness: str = "high") -> dict[str, Any]:
    jpeg = _prep(img)
    key = f"{_h(jpeg)}-{strictness}"
    raw, hit = _cached(client, "skin_tone", key, lambda: client.run_task(
        PATHS["skin_tone"], {"face_angle_strictness_level": strictness}, file_bytes=jpeg))
    c = _results(raw).get("color") or {}
    return {"tone": c.get("skin_color"), "skin_color": c.get("skin_color"),
            "eye_color": c.get("eye_color"), "eye_color_name": c.get("eye_color_name"),
            "lip_color": c.get("lip_color"), "eyebrow_color": c.get("eyebrow_color"),
            "hair_color": c.get("hair_color"), "hair_color_name": c.get("hair_color_name"),
            "raw": raw, "units": 0.0 if hit else COSTS["skin_tone"], "cached": hit}


def fitzpatrick(client: YouCamClient, img: bytes) -> dict[str, Any]:
    jpeg = _prep(img)
    key = _h(jpeg)
    raw, hit = _cached(client, "fitzpatrick", key, lambda: client.run_task(
        PATHS["fitzpatrick"], {"version": "1.0"}, file_bytes=jpeg))
    t = _results(raw).get("fitzpatrick_scale")
    return {"type": t, "raw": raw, "units": 0.0 if hit else COSTS["fitzpatrick"], "cached": hit}


def face_attributes(client: YouCamClient, img: bytes, *, features: list[str] | None = None,
                    strictness: str = "flexible") -> dict[str, Any]:
    """Pose/quality and facial ratios. Keep features <= 5 to pay 10 units."""
    feats = list(features or DEFAULT_FACE_FEATURES)
    jpeg = _prep(img)
    key = f"{_h(jpeg)}-{strictness}-{hashlib.sha256(','.join(sorted(feats)).encode()).hexdigest()[:10]}"
    raw, hit = _cached(client, "face_attributes", key, lambda: client.run_task(
        PATHS["face_attributes"], {"features": feats, "face_angle_strictness_level": strictness},
        file_bytes=jpeg))
    res = _results(raw)
    q = res.get("face_quality") or {}
    ratio = res.get("facialratio") or {}
    out = {"yaw": None, "pitch": None, "roll": None,
           "face_ratio": ratio.get("face_aspect_ratio"),
           "face_quality": q, "facialratio": ratio,
           "frontal": q.get("frontal"), "lighting": q.get("lighting"), "faceangle": q.get("faceangle"),
           "raw": raw, "units": 0.0 if hit else estimated_cost("face_attributes", len(feats)),
           "cached": hit}
    for ang in ("yaw", "pitch", "roll"):
        if isinstance(q.get(ang), (int, float)):
            out[ang] = q[ang]
    return out


def _clean_targets(targets: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in targets.items():
        k = SIM_ALIASES.get(k, k)
        if k not in SIM_CONCERNS:
            continue  # not simulatable (e.g. moisture, firmness)
        v = max(0.0, min(1.0, float(v)))
        if v > 0:
            out[k] = round(v, 2)
    if not out:
        raise YouCamError("InvalidParameters", "Pick at least one concern the simulation supports "
                          f"({', '.join(SIM_CONCERNS)}) with a strength above 0.")
    return out


def skin_simulation(client: YouCamClient, img: bytes, targets: dict[str, float]) -> dict[str, Any]:
    """Simulate improvement. ALWAYS label the result 'Simulation, not your result'."""
    t = _clean_targets(targets)
    jpeg = _prep(img)
    key = f"{_h(jpeg)}-{hashlib.sha256(json.dumps(t, sort_keys=True).encode()).hexdigest()[:12]}"
    raw, hit = _cached(client, "skin_simulation", key, lambda: client.run_task(
        PATHS["skin_simulation"], dict(t), file_bytes=jpeg))
    image = _fetch_image(client, raw, "skin_simulation", key)
    return {"image": image, "targets": t, "raw": raw, "label": "Simulation, not your result",
            "units": 0.0 if hit else estimated_cost("skin_simulation", len(t)), "cached": hit}


def photo_lighting(client: YouCamClient, img: bytes) -> dict[str, Any]:
    jpeg = _prep(img)
    key = _h(jpeg)
    raw, hit = _cached(client, "photo_lighting", key, lambda: client.run_task(
        PATHS["photo_lighting"], {}, file_bytes=jpeg))
    image = _fetch_image(client, raw, "photo_lighting", key)
    return {"image": image, "raw": raw, "units": 0.0 if hit else COSTS["photo_lighting"], "cached": hit}


def text_to_image(client: YouCamClient, prompt: str, *, negative_prompt: str | None = None,
                  size: str = "1328*1328", prompt_extend: bool = False,
                  with_meta: bool = False) -> bytes | dict[str, Any]:
    """Text-to-Image V2. Returns image bytes (or a dict with units when with_meta=True)."""
    body: dict[str, Any] = {"model": "youcam-image-v2", "prompt": prompt, "size": size,
                            "prompt_extend": prompt_extend}
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    raw, hit = _cached(client, "text_to_image", key,
                       lambda: client.run_task(PATHS["text_to_image"], body))
    image = _fetch_image(client, raw, "text_to_image", key)
    if with_meta:
        return {"image": image, "raw": raw, "units": 0.0 if hit else COSTS["text_to_image"], "cached": hit}
    return image
