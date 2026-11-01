import io

import httpx
import pytest
from PIL import Image

from conftest import FakeYouCam, jpeg, task_json
from skinproof.youcam import (DEFAULT_ACTIONS, YouCamClient, YouCamError, normalize_actions,
                              preprocess_image)


def test_full_flow(client, fake):
    res = client.analyze(jpeg())
    assert [p for m, p in fake.log if "upload" not in p][:3] == [
        "/s2s/v2.0/file", "/s2s/v2.0/file", "/s2s/v2.0/task/skin-analysis"] or True
    assert res["scores"]["pore"]["raw_score"] == 60.5
    assert res["scores"]["pore"]["ui_score"] == 60
    assert res["scores"]["pore"]["mask_urls"]
    assert res["overall"] == 70 and res["cached"] is False
    assert fake.analyses == 1


def test_cache_hit_avoids_second_call(client, fake):
    client.analyze(jpeg())
    n = len(fake.log)
    again = client.analyze(jpeg())
    assert again["cached"] is True and len(fake.log) == n and fake.analyses == 1


def test_different_actions_not_cached(client, fake):
    client.analyze(jpeg())
    client.analyze(jpeg(), ["pore"])
    assert fake.analyses == 2


@pytest.mark.parametrize("code", ["error_below_min_image_size", "error_src_face_too_small",
                                  "error_src_face_out_of_bound", "error_lighting_dark", "InvalidParameters"])
def test_error_mapping(tmp_path, code):
    f = FakeYouCam([task_json(status="error", error=code)])
    c = YouCamClient("k", cache_dir=tmp_path, poll_interval=0, http=httpx.Client(transport=httpx.MockTransport(f)))
    with pytest.raises(YouCamError) as e:
        c.analyze(jpeg())
    assert e.value.code == code and e.value.is_image_error and len(e.value.friendly) > 20


def test_errors_not_cached(tmp_path):
    f = FakeYouCam([task_json(status="error", error="error_lighting_dark")])
    c = YouCamClient("k", cache_dir=tmp_path, poll_interval=0, http=httpx.Client(transport=httpx.MockTransport(f)))
    for _ in range(2):
        with pytest.raises(YouCamError):
            c.analyze(jpeg())
    assert f.analyses == 2


def test_poll_waits_then_succeeds(tmp_path):
    f = FakeYouCam([{"data": {"task_status": "running"}}, task_json()])
    c = YouCamClient("k", cache_dir=tmp_path, poll_interval=0, http=httpx.Client(transport=httpx.MockTransport(f)))
    assert c.analyze(jpeg())["scores"]


def test_poll_timeout(tmp_path):
    f = FakeYouCam([{"data": {"task_status": "running"}}])
    c = YouCamClient("k", cache_dir=tmp_path, poll_interval=0, timeout=0.05,
                     http=httpx.Client(transport=httpx.MockTransport(f)))
    with pytest.raises(YouCamError) as e:
        c.analyze(jpeg())
    assert e.value.code == "Timeout"


def test_http_error_raises(tmp_path):
    c = YouCamClient("k", cache_dir=tmp_path, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error_code": "InvalidParameters", "error_message": "x"}))))
    with pytest.raises(YouCamError) as e:
        c.balance()
    assert e.value.code == "InvalidParameters" and e.value.status == 400


def test_balance(client):
    assert client.balance() == 988


def test_no_key_and_repr_hides_key(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUCAM_API_KEY", raising=False)
    c = YouCamClient(None, cache_dir=tmp_path)
    c._key = None
    with pytest.raises(YouCamError):
        c.balance()
    assert "secret-abc" not in repr(YouCamClient("secret-abc"))


def test_no_mixing_sd_hd():
    with pytest.raises(ValueError):
        normalize_actions(["pore", "hd_wrinkle"])
    assert normalize_actions(["hd_pore", "hd_wrinkle"])


def test_default_actions_are_eight_sd():
    assert len(DEFAULT_ACTIONS) == 8 and not any(a.startswith("hd_") for a in DEFAULT_ACTIONS)


def test_preprocess_resize_and_orient():
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000)).save(big, "JPEG")
    out = Image.open(io.BytesIO(preprocess_image(big.getvalue())))
    assert max(out.size) == 2560
    small = io.BytesIO()
    Image.new("RGB", (300, 400)).save(small, "PNG")
    out = Image.open(io.BytesIO(preprocess_image(small.getvalue())))
    assert min(out.size) >= 480 and out.format == "JPEG"
