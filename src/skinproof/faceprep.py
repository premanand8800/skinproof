"""Face auto-crop before YouCam.

Phone selfies often show a small face in a big frame and YouCam rejects them with
error_src_face_too_small. We crop to the largest frontal face (Haar cascade) so the
face is about 72% of the crop width, 3:4 portrait. No face found -> original bytes.
"""
from __future__ import annotations

import io

from PIL import Image, ImageOps

FACE_FRAC = 0.72
_cascade = None


def _get_cascade():
    global _cascade
    if _cascade is None:
        import cv2
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _cascade


def crop_to_face(data: bytes, cascade=None) -> bytes:
    """Return JPEG bytes cropped around the largest face, or `data` unchanged."""
    try:
        import numpy as np
        import cv2
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        arr = np.asarray(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        faces = (cascade or _get_cascade()).detectMultiScale(gray, 1.1, 6, minSize=(200, 200))
        if faces is None or len(faces) == 0:
            return data
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        cx, cy = x + w / 2, y + h / 2 * 1.05
        cw = w / FACE_FRAC
        ch = cw * 4 / 3
        left, top = max(0, cx - cw / 2), max(0, cy - ch * 0.48)
        box = (int(left), int(top), int(min(img.width, left + cw)), int(min(img.height, top + ch)))
        if box[2] - box[0] < 50 or box[3] - box[1] < 50:
            return data
        out = io.BytesIO()
        img.crop(box).save(out, "JPEG", quality=95)
        return out.getvalue()
    except Exception:
        return data
