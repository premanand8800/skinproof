import io
import json

import httpx
import pytest
from PIL import Image


def jpeg(color=(180, 140, 120), size=(900, 1100)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


def task_json(scores=None, status="success", error=None):
    scores = scores or {"pore": 60.5, "redness": 55.0}
    d = {"task_status": status}
    if error:
        d["error"] = error
    else:
        d["results"] = {"output": [
            {"type": t, "ui_score": int(v), "raw_score": v, "mask_urls": [f"https://cdn.test/{t}.png"]}
            for t, v in scores.items()] + [{"type": "all", "ui_score": 70}]}
    return {"status": 200, "data": d}


class FakeYouCam:
    """httpx MockTransport handler recording calls."""
    def __init__(self, tasks=None):
        self.tasks = list(tasks or [task_json()])
        self.log = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.log.append((req.method, req.url.path))
        p = req.url.path
        if p == "/s2s/v2.0/file":
            return httpx.Response(200, json={"data": {"files": [{"file_id": "f1", "requests": [
                {"method": "PUT", "url": "https://upload.test/put", "headers": {"Content-Type": "image/jpeg"}}]}]}})
        if req.url.host == "upload.test":
            return httpx.Response(200)
        if p == "/s2s/v2.0/task/skin-analysis" and req.method == "POST":
            body = json.loads(req.content)
            assert body["src_file_id"] == "f1" and body["miniserver_args"]["enable_mask_overlay"]
            return httpx.Response(200, json={"data": {"task_id": "t1"}})
        if p == "/s2s/v2.0/task/skin-analysis/t1":
            t = self.tasks.pop(0) if len(self.tasks) > 1 else self.tasks[0]
            return httpx.Response(200, json=t)
        if p == "/s2s/v1.0/client/credit":
            return httpx.Response(200, json={"results": [{"amount": 988}]})
        if req.url.host == "cdn.test":
            return httpx.Response(200, content=b"PNG")
        return httpx.Response(404, json={"error": "nope"})

    @property
    def analyses(self):
        return sum(1 for m, p in self.log if m == "POST" and p == "/s2s/v2.0/task/skin-analysis")


@pytest.fixture
def fake():
    return FakeYouCam()


@pytest.fixture
def client(fake, tmp_path):
    from skinproof.youcam import YouCamClient
    return YouCamClient("test-key", cache_dir=tmp_path / "cache", poll_interval=0,
                        http=httpx.Client(transport=httpx.MockTransport(fake)))
