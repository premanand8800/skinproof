"""FastAPI app. The API key lives only on the server."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import re

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
    claim_text: str | None = None
    claim_url: str | None = None


def _flat(res: dict) -> dict[str, float]:
    return {c: v["raw_score"] for c, v in res["scores"].items() if v.get("raw_score") is not None}


def create_app(client: YouCamClient | None = None, store: Store | None = None,
               demo_path: Path | None = None, agent_deps: dict | None = None,
               demo_agent_path: Path | None = None) -> FastAPI:
    """agent_deps: optional overrides {chat, extra, claims, budget_factory} (tests)."""
    app = FastAPI(title="SkinProof")
    client = client or YouCamClient(cache_dir=ROOT / ".cache" / "youcam")
    store = store or Store(ROOT / "private")
    demo_path = demo_path or ROOT / "demo" / "profile.json"
    demo_agent_path = demo_agent_path or ROOT / "demo" / "agent_trace.json"
    agent_deps = agent_deps or {}
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
            raise HTTPException(400, f"Upload {noise.MIN_RETAKES} to 6 photos of the same face taken the same day "
                                     f"(5 to 6 with slight head turns is best).")
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
    async def add_product(pid: str, request: Request):
        """JSON body, or multipart form (adds an optional `label` image of the product)."""
        get_profile(pid)
        label = None
        ctype = request.headers.get("content-type", "")
        if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
            form = await request.form()
            raw = form.get("concerns") or ""
            body = ProductIn(name=str(form.get("name") or ""), start_date=form.get("start_date") or None,
                             concerns=[c.strip() for c in str(raw).split(",") if c.strip()],
                             claim_text=form.get("claim_text") or None, claim_url=form.get("claim_url") or None)
            up = form.get("label")
            if up is not None and hasattr(up, "read"):
                label = await up.read() or None
        else:
            try:
                body = ProductIn(**(await request.json()))
            except Exception:
                raise HTTPException(422, "Bad product body")
        if not body.name.strip():
            raise HTTPException(422, "Product name required")
        prod = store.add_product(pid, body.name, body.start_date or date.today().isoformat(),
                                 [c for c in body.concerns if c in CONCERN_LABELS])
        extra = {k: v for k, v in (("claim_text", body.claim_text), ("claim_url", body.claim_url)) if v}
        if label:
            extra["claim_image"] = store.save_photo(pid, "label", label)
        if extra:
            def upd(p):
                next(x for x in p["products"] if x["id"] == prod["id"]).update(extra)
            store._mutate(pid, upd)
            prod.update(extra)
        return prod

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

    @app.post("/api/profiles/{pid}/agent")
    async def run_agent(pid: str, file: UploadFile | None = File(None), product_id: str | None = Form(None),
                        when: str | None = Form(None)):
        from .agent import Agent, sse
        from .budget import UnitBudget
        p = get_profile(pid)
        if not p["products"]:
            raise HTTPException(409, "Add a product first")
        if product_id and not any(x["id"] == product_id for x in p["products"]):
            raise HTTPException(404, "Unknown product")
        if when:
            try:
                date.fromisoformat(when)
            except ValueError:
                raise HTTPException(400, "Bad date")
        photo = (await file.read()) if file is not None else None
        agent = Agent(store=store, client=client, profile_id=pid, product_id=product_id, photo=photo or None,
                      when=when, chat=agent_deps.get("chat"), extra=agent_deps.get("extra"),
                      claims=agent_deps.get("claims"),
                      budget=agent_deps["budget_factory"]() if "budget_factory" in agent_deps else UnitBudget())

        def stream():
            try:
                for ev in agent.run():
                    yield sse(ev)
            except Exception as e:  # never leave the stream hanging
                yield sse({"type": "error", "step": agent.step, "summary": f"Agent crashed: {type(e).__name__}",
                           "units_spent": agent.budget.spent, "units_left_in_budget": agent.budget.left})
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": agent.run_id})

    @app.get("/api/agent/runs/{run_id}")
    def agent_run(run_id: str):
        if not re.fullmatch(r"[a-f0-9]{6,32}", run_id):
            raise HTTPException(404)
        path = store.root / "runs" / f"{run_id}.json"
        if not path.is_file():
            raise HTTPException(404, "Unknown run")
        return json.loads(path.read_text())

    @app.get("/api/demo/agent")
    def demo_agent():
        return json.loads(demo_agent_path.read_text())

    @app.get("/api/demo/agent/live")
    def demo_agent_live():
        """The recorded live run on the AI-generated face, with its lesson (events unmodified)."""
        return json.loads((ROOT / "demo" / "agent_trace_live_synthetic.json").read_text())

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
