"""FastAPI app. The API key lives only on the server."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import noise
from .store import Store
from .verdict import compute_verdicts
from .youcam import DEFAULT_ACTIONS, YouCamClient, YouCamError, CONCERN_LABELS

ROOT = Path(__file__).resolve().parents[2]


class ProfileIn(BaseModel):
    name: str = "Me"


class ProductIn(BaseModel):
    name: str
    start_date: str | None = None
    concerns: list[str] = []


def _flat(res: dict) -> dict[str, float]:
    return {c: v["raw_score"] for c, v in res["scores"].items() if v.get("raw_score") is not None}


def create_app(client: YouCamClient | None = None, store: Store | None = None,
               demo_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="SkinProof")
    client = client or YouCamClient(cache_dir=ROOT / ".cache" / "youcam")
    store = store or Store(ROOT / "private")
    demo_path = demo_path or ROOT / "demo" / "profile.json"
    app.state.client, app.state.store = client, store

    def units() -> float | None:
        try:
            return client.balance()
        except Exception:
            return None

    def get_profile(pid: str) -> dict:
        p = store.get_profile(pid)
        if not p:
            raise HTTPException(404, "Unknown profile")
        return p

    def grab_masks(pid: str, res: dict) -> dict[str, str]:
        """Best-effort: keep a local copy of the first mask overlay per concern."""
        fetch = getattr(client, "download_bytes", None)
        paths = {}
        if not fetch:
            return paths
        for c, v in res["scores"].items():
            if v.get("mask_urls"):
                try:
                    data = fetch(v["mask_urls"][0])
                    if data:
                        paths[c] = store.save_photo(pid, "mask", data)
                except Exception:
                    pass
        return paths

    def run_analysis(data: bytes) -> dict:
        return client.analyze(data, DEFAULT_ACTIONS)

    @app.get("/api/status")
    def status():
        return {"has_key": client.has_key, "units": units() if client.has_key else None,
                "concerns": {c: CONCERN_LABELS[c] for c in DEFAULT_ACTIONS},
                "units_per_analysis": 12}

    @app.post("/api/profiles")
    def new_profile(body: ProfileIn):
        return store.create_profile(body.name)

    @app.get("/api/profiles/{pid}")
    def profile(pid: str):
        return get_profile(pid)

    @app.post("/api/profiles/{pid}/calibrate")
    async def calibrate(pid: str, files: list[UploadFile] = File(...)):
        get_profile(pid)
        if len(files) < noise.MIN_RETAKES or len(files) > 6:
            raise HTTPException(400, "Upload 3 to 6 photos of the same face taken the same day.")
        good, rejected = [], []
        for i, f in enumerate(files):
            data = await f.read()
            try:
                res = run_analysis(data)
                good.append(_flat(res))
                store.save_photo(pid, "calib", data)
            except YouCamError as e:
                rejected.append({"index": i, "code": e.code, "message": e.friendly})
            except Exception as e:
                rejected.append({"index": i, "code": "BadImage", "message": "Could not read this image."})
        if len(good) < noise.MIN_RETAKES:
            return JSONResponse({"error": "Not enough usable photos to measure noise.",
                                 "rejected": rejected, "usable": len(good)}, status_code=422)
        cal = noise.build_calibration(real=good)
        store.set_calibration(pid, cal)
        return {"calibration": cal, "rejected": rejected, "units": units()}

    @app.post("/api/profiles/{pid}/products")
    def add_product(pid: str, body: ProductIn):
        get_profile(pid)
        return store.add_product(pid, body.name, body.start_date or date.today().isoformat(),
                                 [c for c in body.concerns if c in CONCERN_LABELS])

    @app.post("/api/profiles/{pid}/checkins")
    async def checkin(pid: str, file: UploadFile = File(...), when: str | None = Form(None)):
        get_profile(pid)
        data = await file.read()
        when = when or date.today().isoformat()
        try:
            date.fromisoformat(when)
        except ValueError:
            raise HTTPException(400, "Bad date")
        rec = {"date": when}
        try:
            res = run_analysis(data)
            rec["photo"] = store.save_photo(pid, "checkin", data)
            rec["scores"] = _flat(res)
            rec["masks"] = grab_masks(pid, res)
            rec["cached"] = res.get("cached", False)
        except YouCamError as e:
            if not e.is_image_error:
                raise HTTPException(502, e.friendly)
            rec["error"] = {"code": e.code, "message": e.friendly}
        except Exception:
            rec["error"] = {"code": "BadImage", "message": "Could not read this image."}
        saved = store.add_checkin(pid, rec)
        return {"checkin": saved, "counted": "error" not in rec, "units": units()}

    @app.get("/api/profiles/{pid}/results")
    def results(pid: str, product_id: str | None = None):
        p = get_profile(pid)
        if not p["calibration"]:
            raise HTTPException(409, "Calibrate first")
        prod = next((x for x in p["products"] if x["id"] == product_id), None) \
            or (p["products"][-1] if p["products"] else None)
        v = compute_verdicts(p["checkins"], noise.thresholds(p["calibration"]), prod)
        v["product"] = prod
        v["checkins"] = [{k: c.get(k) for k in ("id", "date", "error", "masks")} for c in p["checkins"]]
        return v

    @app.get("/api/demo")
    def demo():
        d = json.loads(demo_path.read_text())
        cal = noise.build_calibration(real=d["retakes"])
        v = compute_verdicts(d["checkins"], noise.thresholds(cal), d["product"])
        v.update({"product": d["product"], "calibration": cal, "sample_data": True,
                  "note": d["note"], "checkins": [{"id": c["id"], "date": c["date"]} for c in d["checkins"]]})
        return v

    @app.get("/api/files/{path:path}")
    def files(path: str):
        full = (store.root / path).resolve()
        if store.root.resolve() not in full.parents or not full.is_file():
            raise HTTPException(404)
        return FileResponse(full)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    return app


def app_factory() -> FastAPI:
    return create_app()
