import io

import numpy as np
from PIL import Image

from skinproof import faceprep


def _jpeg(w=800, h=1000, color=(120, 90, 80)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


class FakeCascade:
    def __init__(self, faces):
        self.faces = faces
        self.args = None

    def detectMultiScale(self, gray, scale, neighbors, minSize=None):
        self.args = (scale, neighbors, minSize)
        return np.array(self.faces, dtype=int).reshape(-1, 4)


def test_no_face_returns_original():
    data = _jpeg()
    assert faceprep.crop_to_face(data, cascade=FakeCascade([])) == data


def test_real_cascade_on_blank_image_returns_original():
    data = _jpeg()
    assert faceprep.crop_to_face(data) == data


def test_garbage_bytes_returned_unchanged():
    assert faceprep.crop_to_face(b"not an image") == b"not an image"


def test_crop_geometry_with_mocked_cascade():
    data = _jpeg(1200, 1600)
    cas = FakeCascade([(400, 500, 300, 300), (100, 100, 220, 220)])  # largest wins
    out = faceprep.crop_to_face(data, cascade=cas)
    assert cas.args == (1.1, 6, (200, 200))
    im = Image.open(io.BytesIO(out))
    assert im.format == "JPEG"
    assert abs(im.width - 300 / 0.72) <= 1
    assert abs(im.height / im.width - 4 / 3) < 0.01
    assert out != data


def test_crop_clipped_to_image():
    data = _jpeg(800, 1000)
    out = faceprep.crop_to_face(data, cascade=FakeCascade([(0, 0, 250, 250)]))
    im = Image.open(io.BytesIO(out))
    assert im.width <= 800 and im.height <= 1000
