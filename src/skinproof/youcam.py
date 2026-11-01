"""Thin, cache-first client for the YouCam (Perfect Corp) AI Skin Analysis API v2.

The API key is read from the environment (or a local .env) and is never logged
or echoed. Every analysis is content-addressed on disk so an identical image
with identical actions is never paid for twice.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

BASE_URL = "https://yce-api-01.makeupar.com"

# SD actions; HD actions are the same names with an "hd_" prefix. Never mix.
SD_ACTIONS = [
    "wrinkle", "droopy_upper_eyelid", "droopy_lower_eyelid", "firmness", "acne",
    "moisture", "eye_bag", "dark_circle_v2", "age_spot", "radiance", "redness",
    "oiliness", "pore", "texture", "tear_trough", "skin_type",
]
DEFAULT_ACTIONS = ["wrinkle", "pore", "texture", "acne", "redness", "age_spot",
                   "dark_circle_v2", "moisture"]

CONCERN_LABELS = {
    "wrinkle": "Wrinkles", "pore": "Pores", "texture": "Texture", "acne": "Acne",
    "redness": "Redness", "age_spot": "Age spots", "dark_circle_v2": "Dark circles",
    "moisture": "Moisture", "firmness": "Firmness", "radiance": "Radiance",
    "oiliness": "Oiliness", "eye_bag": "Eye bags", "tear_trough": "Tear troughs",
    "droopy_upper_eyelid": "Upper eyelid", "droopy_lower_eyelid": "Lower eyelid",
    "skin_type": "Skin type",
}

# error code -> friendly retake advice
FRIENDLY_ERRORS = {
    "error_below_min_image_size": "The photo is too small. Use a photo at least 480 px on its short side.",
    "error_src_face_too_small": "Your face is too small in the frame. It should fill more than 60% of the photo width.",
    "error_src_face_out_of_bound": "Your face is cut off by the edge of the photo. Keep your whole face inside the frame.",
    "error_lighting_dark": "The photo is too dark. Face a window or a bright, even light and retake.",
    "InvalidParameters": "The service rejected the photo or request. Retake with a clear, front-facing face.",
    "error_no_face": "No face was found. Face the camera straight on.",
    "error_face_position_invalid": "Your whole face must be visible, forward-facing and centered.",
    "error_face_position_too_small": "Your face is too small in the frame. Move closer.",
    "error_face_position_out_of_boundary": "Your face touches the edge of the photo. Keep it fully inside the frame.",
    "error_face_not_forward_facing": "Look straight at the camera.",
    "error_face_angle_upward": "Tilt your head slightly down.",
    "error_face_angle_downward": "Tilt your head slightly up.",
    "error_face_angle_leftward": "Turn your head slightly to the right.",
    "error_face_angle_rightward": "Turn your head slightly to the left.",
    "error_face_angle_left_tilt": "Tilt your head slightly to the right.",
    "error_face_angle_right_tilt": "Tilt your head slightly to the left.",
    "error_face_angle_invalid": "Keep your head within 10 degrees of straight.",
    "error_insufficient_lighting": "The photo is too dim. Use bright, even light.",
    "error_exceed_max_image_size": "The photo is too large. Use one under 2560 px on the long side.",
    "error_nsfw_content_detected": "The service rejected this image content.",
}
IMAGE_ERROR_CODES = set(FRIENDLY_ERRORS)


class YouCamError(Exception):
    def __init__(self, code: str, message: str | None = None, *, status: int | None = None):
        self.code = code
        self.status = status
        self.friendly = FRIENDLY_ERRORS.get(code, message or code)
        super().__init__(f"{code}: {self.friendly}")

    @property
    def is_image_error(self) -> bool:
        return self.code in IMAGE_ERROR_CODES or self.code.startswith("error_src_")


def load_api_key() -> str | None:
    key = os.environ.get("YOUCAM_API_KEY")
    if key:
        return key.strip()
    env = Path(__file__).resolve().parents[2] / ".env"
    for p in (Path(".env"), env):
        if p.is_file():
            for line in p.read_text().splitlines():
                if line.startswith("YOUCAM_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def preprocess_image(data: bytes, max_side: int = 2560, min_short: int = 480) -> bytes:
    """EXIF-orient, convert to RGB JPEG, cap long side at 2560, floor short side at 480."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    scale = 1.0
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
    elif min(w, h) < min_short:
        scale = min_short / min(w, h)
    if scale != 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def cache_key(image_bytes: bytes, actions: list[str]) -> str:
    h = hashlib.sha256(image_bytes).hexdigest()
    a = hashlib.sha256(",".join(sorted(actions)).encode()).hexdigest()[:12]
    return f"{h}-{a}"


def normalize_actions(actions: list[str]) -> list[str]:
    hd = [a.startswith("hd_") for a in actions]
    if any(hd) and not all(hd):
        raise ValueError("Do not mix SD and HD actions")
    return list(actions)


def parse_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a finished task JSON into {scores, overall, skin_age, raw}."""
    data = raw.get("data", raw)
    results = data.get("results") or {}
    output = results.get("output") if isinstance(results, dict) else results
    scores: dict[str, dict[str, Any]] = {}
    overall = None
    skin_age = None
    for item in output or []:
        t = str(item.get("type", ""))
        key = t[3:] if t.startswith("hd_") else t
        if key in ("all", "overall", "skin_score", "score"):
            overall = item.get("ui_score", item.get("score"))
            continue
        if key == "skin_age":
            skin_age = item.get("ui_score", item.get("value", item.get("skin_age")))
            continue
        if "raw_score" not in item and "ui_score" not in item:
            continue
        ui = item.get("ui_score")
        raw_s = item.get("raw_score", ui)
        scores[key] = {
            "ui_score": ui,
            "raw_score": float(raw_s) if raw_s is not None else None,
            "mask_urls": item.get("mask_urls") or [],
        }
    if isinstance(results, dict):
        if overall is None and isinstance(results.get("all"), dict):
            overall = results["all"].get("ui_score")
        if skin_age is None:
            skin_age = results.get("skin_age")
    return {"scores": scores, "overall": overall, "skin_age": skin_age, "raw": raw}


class YouCamClient:
    def __init__(self, api_key: str | None = None, *, base_url: str = BASE_URL,
                 cache_dir: str | Path = ".cache/youcam", http: httpx.Client | None = None,
                 poll_interval: float = 2.0, timeout: float = 90.0):
        self._key = api_key or load_api_key()
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir)
        self.http = http or httpx.Client(timeout=60)
        self.poll_interval = poll_interval
        self.timeout = timeout

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def __repr__(self) -> str:  # never leak the key
        return f"YouCamClient(base_url={self.base_url!r}, has_key={self.has_key})"

    # -- low level ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise YouCamError("NoApiKey", "YOUCAM_API_KEY is not configured on the server.")
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kw) -> dict[str, Any]:
        r = self.http.request(method, self.base_url + path, headers=self._headers(), **kw)
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code >= 400:
            code = str(body.get("error_code") or body.get("error") or f"HTTP_{r.status_code}")
            msg = body.get("error_message") or body.get("message") or r.reason_phrase
            raise YouCamError(code, str(msg), status=r.status_code)
        return body

    def download_bytes(self, url: str) -> bytes | None:
        """Fetch a pre-signed asset (mask overlay). No auth header: URLs are signed."""
        r = self.http.get(url)
        return r.content if r.status_code == 200 else None

    # -- account -----------------------------------------------------------
    def balance(self) -> float | None:
        body = self._request("GET", "/s2s/v1.0/client/credit")
        results = body.get("results") or body.get("data", {}).get("results") or []
        if isinstance(results, dict):
            results = [results]
        total = sum(float(x.get("amount", 0)) for x in results)
        return total if results else None

    def feature_cost(self) -> dict[str, Any]:
        return self._request("GET", "/s2s/v2.0/credit/feature-cost")

    # -- upload ------------------------------------------------------------
    def upload(self, jpeg: bytes, file_name: str = "selfie.jpg") -> str:
        body = self._request("POST", "/s2s/v2.0/file", json={"files": [
            {"content_type": "image/jpeg", "file_name": file_name, "file_size": len(jpeg)}]})
        f = body["data"]["files"][0]
        req = f["requests"][0]
        r = self.http.request(req.get("method", "PUT"), req["url"],
                              headers=req.get("headers") or {}, content=jpeg)
        if r.status_code >= 400:
            raise YouCamError("UploadFailed", f"Upload failed with HTTP {r.status_code}", status=r.status_code)
        return f["file_id"]

    # -- analysis ----------------------------------------------------------
    def start_task(self, file_id: str, actions: list[str], masks: bool = True) -> str:
        body = self._request("POST", "/s2s/v2.0/task/skin-analysis", json={
            "src_file_id": file_id, "dst_actions": actions,
            "miniserver_args": {"enable_mask_overlay": masks}, "format": "json"})
        return body["data"]["task_id"]

    def poll(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            body = self._request("GET", f"/s2s/v2.0/task/skin-analysis/{task_id}")
            d = body.get("data", {})
            status = d.get("task_status")
            if status == "success":
                return body
            if status == "error":
                raise YouCamError(str(d.get("error") or d.get("error_code") or "TaskError"),
                                  str(d.get("error_message") or "Analysis failed"))
            if time.monotonic() > deadline:
                raise YouCamError("Timeout", f"Analysis did not finish within {self.timeout:.0f}s")
            time.sleep(self.poll_interval)

    # -- generic task runner (used by youcam_extra) -------------------------
    def poll_task(self, path: str, task_id: str) -> dict[str, Any]:
        """Poll GET {path}/{task_id} until success/error. Raises YouCamError on error/timeout."""
        deadline = time.monotonic() + self.timeout
        while True:
            body = self._request("GET", f"{path}/{task_id}")
            d = body.get("data", {})
            status = d.get("task_status")
            if status == "success":
                return body
            if status == "error":
                raise YouCamError(str(d.get("error") or d.get("error_code") or "TaskError"),
                                  str(d.get("error_message") or "Task failed"))
            if time.monotonic() > deadline:
                raise YouCamError("Timeout", f"Task did not finish within {self.timeout:.0f}s")
            time.sleep(self.poll_interval)

    def run_task(self, path: str, body: dict[str, Any], *, file_bytes: bytes | None = None) -> dict[str, Any]:
        """Upload file_bytes (if any, sets body['src_file_id']), POST path, poll to completion."""
        body = dict(body)
        if file_bytes is not None:
            body["src_file_id"] = self.upload(file_bytes)
        resp = self._request("POST", path, json=body)
        return self.poll_task(path, resp["data"]["task_id"])

    def analyze(self, image_bytes: bytes, actions: list[str] | None = None,
                *, use_cache: bool = True) -> dict[str, Any]:
        """Full flow. Returns parse_result() plus 'cached' flag. Cache hits cost nothing."""
        actions = normalize_actions(actions or DEFAULT_ACTIONS)
        jpeg = preprocess_image(image_bytes)
        key = cache_key(jpeg, actions)
        path = self.cache_dir / f"{key}.json"
        if use_cache and path.is_file():
            res = parse_result(json.loads(path.read_text()))
            res["cached"] = True
            return res
        file_id = self.upload(jpeg)
        task_id = self.start_task(file_id, actions)
        raw = self.poll(task_id)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))
        res = parse_result(raw)
        res["cached"] = False
        return res
