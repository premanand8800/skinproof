import json

import httpx
import pytest

from conftest import jpeg
from skinproof import youcam_extra as X
from skinproof.youcam import YouCamClient, YouCamError

RESULTS = {
    "skin-tone-analysis": {"color": {"skin_color": "#b9947c", "eye_color": "#293F9B",
                                     "eye_color_name": "Blue", "lip_color": "#D23245",
                                     "eyebrow_color": "#5B2B31", "hair_color": "#a0a0a0",
                                     "hair_color_name": "Auburn"}},
    "fitzpatrick-scale-analyzer": {"timed": 5, "fitzpatrick_scale": "III"},
    "face-attr-analysis": {"face_quality": {"has_face": True, "frontal": "good", "lighting": "good",
                                            "faceangle": "good", "area": "good"},
                           "facialratio": {"face_aspect_ratio": [1, 1.48]}},
    "skin-simulation": {"url": "https://cdn.test/sim.png"},
    "lighting": {"url": "https://cdn.test/lit.jpg"},
    "text-to-image/youcam": {"url": "https://cdn.test/t2i.jpg"},
}


class Fake:
    def __init__(self, fail=None):
        self.calls = []
        self.posts = {}
        self.fail = fail

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append((req.method, path))
        if path == "/s2s/v2.0/file":
            return httpx.Response(200, json={"data": {"files": [{"file_id": "F1", "requests": [
                {"method": "PUT", "url": "https://upload.test/x", "headers": {}}]}]}})
        if req.url.host == "upload.test":
            return httpx.Response(200)
        if req.url.host == "cdn.test":
            return httpx.Response(200, content=b"IMGBYTES")
        for name, res in RESULTS.items():
            base = f"/s2s/v2.0/task/{name}"
            if path == base and req.method == "POST":
                self.posts[name] = json.loads(req.content)
                return httpx.Response(200, json={"status": 200, "data": {"task_id": "T1"}})
            if path == base + "/T1":
                if self.fail:
                    return httpx.Response(200, json={"data": {"task_status": "error", "error": self.fail}})
                return httpx.Response(200, json={"status": 200, "data": {"task_status": "success",
                                                                          "results": res}})
        return httpx.Response(404, json={})

    @property
    def tasks(self):
        return [c for c in self.calls if "/task/" in c[1] and c[0] == "POST"]


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def cl(tmp_path, fake):
    return YouCamClient("k", cache_dir=tmp_path, poll_interval=0,
                        http=httpx.Client(transport=httpx.MockTransport(fake)))


def test_skin_tone(cl, fake):
    r = X.skin_tone(cl, jpeg())
    assert r["tone"] == "#b9947c" and r["eye_color_name"] == "Blue" and r["units"] == 20
    assert fake.posts["skin-tone-analysis"]["src_file_id"] == "F1"
    again = X.skin_tone(cl, jpeg())
    assert again["units"] == 0 and again["cached"] and len(fake.tasks) == 1


def test_fitzpatrick(cl, fake):
    r = X.fitzpatrick(cl, jpeg())
    assert r["type"] == "III" and r["units"] == 10
    assert fake.posts["fitzpatrick-scale-analyzer"]["version"] == "1.0"
    assert X.fitzpatrick(cl, jpeg())["units"] == 0 and len(fake.tasks) == 1


def test_face_attributes(cl, fake):
    r = X.face_attributes(cl, jpeg())
    assert r["face_ratio"] == [1, 1.48] and r["frontal"] == "good" and r["yaw"] is None
    assert r["units"] == 10
    assert fake.posts["face-attr-analysis"]["features"] == X.DEFAULT_FACE_FEATURES
    assert X.face_attributes(cl, jpeg())["units"] == 0
    r2 = X.face_attributes(cl, jpeg(), features=["age"] * 1 + ["gender", "faceShape", "eyeShape", "noseWidth", "lipShape"])
    assert r2["units"] == 20 and not r2["cached"]


def test_skin_simulation(cl, fake):
    r = X.skin_simulation(cl, jpeg(), {"redness": 0.5, "pore": 0.4, "moisture": 0.9, "acne": 0})
    assert r["image"] == b"IMGBYTES" and r["units"] == 4 and "Simulation" in r["label"]
    assert fake.posts["skin-simulation"]["redness"] == 0.5 and fake.posts["skin-simulation"]["pores"] == 0.4
    assert "moisture" not in fake.posts["skin-simulation"] and "acne" not in fake.posts["skin-simulation"]
    r2 = X.skin_simulation(cl, jpeg(), {"redness": 0.5, "pore": 0.4})
    assert r2["units"] == 0 and r2["image"] == b"IMGBYTES" and len(fake.tasks) == 1


def test_skin_simulation_needs_valid_target(cl):
    with pytest.raises(YouCamError):
        X.skin_simulation(cl, jpeg(), {"moisture": 1.0})
    assert X.estimated_cost("skin_simulation", 5) == 6


def test_photo_lighting(cl, fake):
    r = X.photo_lighting(cl, jpeg())
    assert r["image"] == b"IMGBYTES" and r["units"] == 2
    assert X.photo_lighting(cl, jpeg())["units"] == 0 and len(fake.tasks) == 1


def test_text_to_image(cl, fake):
    assert X.text_to_image(cl, "a face") == b"IMGBYTES"
    body = fake.posts["text-to-image/youcam"]
    assert body["model"] == "youcam-image-v2" and body["prompt"] == "a face" and "src_file_id" not in body
    m = X.text_to_image(cl, "a face", with_meta=True)
    assert m["units"] == 0 and m["cached"]


def test_error_mapped(tmp_path):
    f = Fake(fail="error_face_angle_leftward")
    c = YouCamClient("k", cache_dir=tmp_path, poll_interval=0,
                     http=httpx.Client(transport=httpx.MockTransport(f)))
    with pytest.raises(YouCamError) as e:
        X.skin_tone(c, jpeg())
    assert e.value.is_image_error and "right" in e.value.friendly


def test_estimated_cost():
    assert X.estimated_cost("skin_tone") == 20 and X.estimated_cost("photo_lighting") == 2
    with pytest.raises(KeyError):
        X.estimated_cost("nope")


def test_unreadable_image(cl):
    with pytest.raises(YouCamError):
        X.fitzpatrick(cl, b"not an image")
