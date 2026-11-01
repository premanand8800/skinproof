"""FastAPI app. The API key lives only on the server."""
from __future__ import annotations

import io
import json
import logging
from datetime import date
from pathlib import Path

import re

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from PIL import Image

from . import buycheck, claims as claims_mod, noise
from .guard import Guard, GuardLimit, UNITS_PER_ANALYSIS
from .faceprep import crop_to_face
from .store import Store
from .verdict import compute_verdicts
from .youcam import DEFAULT_ACTIONS, YouCamClient, YouCamError, CONCERN_LABELS

ROOT = Path(__file__).resolve().parents[2]
MAX_UPLOAD = 10 * 1024 * 1024
MAX_CALIB_FILES = 6
PID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")
FILE_RE = re.compile(r"[a-z0-9_-]+-[a-f0-9]{8}\.jpg")
AGENT_RESERVE_PHOTO, AGENT_RESERVE_NO_PHOTO = 40, 4
log = logging.getLogger("skinproof")


def client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "") or "unknown").strip()[:64]


async def read_image(f: UploadFile) -> bytes:
    """Read an upload with a size cap and verify it really is an image."""
    data = await f.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Photo is larger than 10 MB.")
    if not data:
        raise HTTPException(400, "Empty file.")
    if f.content_type and not f.content_type.lower().startswith("image/"):
        raise HTTPException(415, "Only image files are accepted.")
    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception:
        raise HTTPException(415, "That file is not a readable image.")
    return data


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
               demo_agent_path: Path | None = None, guard: Guard | None = None) -> FastAPI:
    """agent_deps: optional overrides {chat, extra, claims, budget_factory} (tests)."""
    app = FastAPI(title="SkinProof")
    client = client or YouCamClient(cache_dir=ROOT / ".cache" / "youcam")
    store = store or Store(ROOT / "private")
    demo_path = demo_path or ROOT / "demo" / "profile.json"
    demo_agent_path = demo_agent_path or ROOT / "demo" / "agent_trace.json"
    agent_deps = agent_deps or {}
    guard = guard or Guard(store.root / "guard.json")
    app.state.client, app.state.store, app.state.guard = client, store, guard

    @app.exception_handler(GuardLimit)
    async def _limit(request: Request, exc: GuardLimit):
        return JSONResponse({"error": "limit", "kind": exc.kind, "detail": str(exc), "live_units_left": guard.units_left(),
                             "fallback": ["sample", "volunteer"]}, status_code=429)

    @app.exception_handler(Exception)
    async def _boom(request: Request, exc: Exception):
        log.error("unhandled %s on %s", type(exc).__name__, request.url.path)
        return JSONResponse({"detail": "Something went wrong on the server. Please try again."}, status_code=500)

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
                "units_per_analysis": UNITS_PER_ANALYSIS, "live_units_left": guard.units_left()}

    @app.post("/api/profiles")
    def new_profile(body: ProfileIn):
        return store.create_profile(body.name)

    @app.get("/api/profiles/{pid}")
    def profile(pid: str):
        return get_profile(pid)

    @app.delete("/api/profiles/{pid}")
    def delete_profile(pid: str):
        get_profile(pid)
        store.delete_profile(pid)
        return {"deleted": True}

    @app.post("/api/profiles/{pid}/calibrate")
    async def calibrate(pid: str, request: Request, files: list[UploadFile] = File(...)):
        get_profile(pid)
        if len(files) < noise.MIN_RETAKES or len(files) > MAX_CALIB_FILES:
            raise HTTPException(400, f"Upload {noise.MIN_RETAKES} to 6 photos of the same face taken the same day "
                                     f"(5 to 6 with slight head turns is best).")
        raws = [await read_image(f) for f in files]
        reserved = UNITS_PER_ANALYSIS * len(raws)
        guard.reserve_units(client_ip(request), reserved)
        good, rejected = [], []
        for i, raw in enumerate(raws):
            data = crop_to_face(raw)
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
                label = await read_image(up)
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
    async def checkin(pid: str, request: Request, file: UploadFile = File(...), when: str | None = Form(None)):
        get_profile(pid)
        data = crop_to_face(await read_image(file))
        when = when or date.today().isoformat()
        try:
            date.fromisoformat(when)
        except ValueError:
            raise HTTPException(400, "Bad date")
        ip = client_ip(request)
        guard.reserve_units(ip, UNITS_PER_ANALYSIS)
        rec = {"date": when}
        try:
            res = run_analysis(data)
            rec["photo"] = store.save_photo(pid, "checkin", data)
            rec["scores"] = _flat(res)
            rec["masks"] = grab_masks(pid, res)
            rec["cached"] = res.get("cached", False)
        except YouCamError as e:
            if not e.is_image_error:
                guard.refund_units(ip, UNITS_PER_ANALYSIS)
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
    async def run_agent(pid: str, request: Request, file: UploadFile | None = File(None), product_id: str | None = Form(None),
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
        photo = crop_to_face(await read_image(file)) if file is not None else None
        ip = client_ip(request)
        reserve = AGENT_RESERVE_PHOTO if photo else AGENT_RESERVE_NO_PHOTO
        guard.reserve_agent(ip, reserve)
        agent = Agent(store=store, client=client, profile_id=pid, product_id=product_id, photo=photo or None,
                      when=when, chat=agent_deps.get("chat"), extra=agent_deps.get("extra"),
                      claims=agent_deps.get("claims"),
                      budget=agent_deps["budget_factory"]() if "budget_factory" in agent_deps else UnitBudget())

        def stream():
            try:
                for ev in agent.run():
                    yield sse(ev)
            except Exception as e:  # never leave the stream hanging
                log.error("agent crashed: %s", type(e).__name__)
                yield sse({"type": "error", "step": agent.step, "summary": "The agent stopped unexpectedly.",
                           "units_spent": agent.budget.spent, "units_left_in_budget": agent.budget.left})
            finally:
                guard.settle_agent(ip, reserve, agent.budget.spent, agent.budget.llm_cost)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": agent.run_id})

    @app.get("/api/agent/runs/{run_id}")
    def agent_run(run_id: str):
        if not re.fullmatch(r"[a-f0-9]{12,32}", run_id):
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

    @app.get("/api/demo/agent/volunteer")
    def demo_agent_volunteer():
        """Real run on a consenting volunteer: two selfies 10 seconds apart."""
        return json.loads((ROOT / "demo" / "agent_trace_live_volunteer.json").read_text())

    @app.get("/demo/volunteer/{name}")
    def demo_volunteer_file(name: str):
        full = (ROOT / "demo" / "volunteer" / name).resolve()
        if full.parent != (ROOT / "demo" / "volunteer").resolve() or not full.is_file():
            raise HTTPException(404)
        return FileResponse(full)

    @app.get("/api/files/{path:path}")
    def files(path: str):
        parts = path.split("/")
        if len(parts) != 2 or not PID_RE.fullmatch(parts[0]) or not FILE_RE.fullmatch(parts[1]) \
                or not store.get_profile(parts[0]):
            raise HTTPException(404)
        base = (store.root / parts[0])
        full = base / parts[1]
        if base.is_symlink() or full.is_symlink() or not full.is_file() \
                or full.resolve().parent != base.resolve():
            raise HTTPException(404)
        return FileResponse(full, headers={"Cache-Control": "private, no-store"})

    @app.get("/api/demo/volunteer/extras")
    def demo_volunteer_extras():
        """Extra YouCam outputs on the volunteer's before photo, made after the recorded run."""
        f = ROOT / "demo" / "volunteer" / "extras.json"
        if not f.is_file():
            raise HTTPException(404)
        return json.loads(f.read_text())

    @app.get("/favicon.svg")
    def favicon():
        return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/static/{name}")
    def static_asset(name: str):
        # only flat, whitelisted public assets (logo/favicon)
        if name not in {"logo.svg", "favicon.svg", "extras.js"}:
            raise HTTPException(404)
        mt = "text/javascript" if name.endswith(".js") else "image/svg+xml"
        return FileResponse(ROOT / "static" / name, media_type=mt)

    @app.get("/api/buy-check/example")
    def buy_check_example():
        d = json.loads((ROOT / "demo" / "claims_example_url.json").read_text())
        d["claims"] = claims_mod.validate_claims(d.get("claims"))
        return {**buycheck.build(d), "example": True, "cost": 0.0, "url": d.get("url")}

    @app.post("/api/buy-check")
    async def buy_check(request: Request, text: str | None = Form(None), url: str | None = Form(None),
                        label: UploadFile | None = File(None)):
        image = await read_image(label) if label is not None and label.filename else None
        text, url = (text or "").strip() or None, (url or "").strip() or None
        if not (image or url or text):
            raise HTTPException(422, "Paste a product link or the claim text, or add a label photo.")
        if url and not image:
            try:
                url = buycheck.check_url(url)
            except ValueError as e:
                raise HTTPException(422, str(e))
        cache = buycheck.Cache(store.root)
        key = buycheck.cache_key(text, url, image)
        hit = cache.get(key)
        if hit:
            return {**hit, "cached": True, "cost": 0.0}
        ip = client_ip(request)
        guard.reserve_llm(ip, 0.02)
        try:
            ext = (agent_deps.get("claims") or claims_mod).extract_claims(text=text, url=url, image=image,
                                            mime=(label.content_type if image and label.content_type else "image/jpeg"))
        except Exception as e:
            guard.settle_llm(ip, 0.02, 0.0, failed=True)
            log.error("buy-check failed %s", type(e).__name__)
            raise HTTPException(502, "Could not read claims from that source. Try pasting the claim text instead.")
        guard.settle_llm(ip, 0.02, ext.get("cost") or 0.0)
        res = buycheck.build(ext)
        if url:
            res["url"] = url
        cache.put(key, res)
        return {**res, "cached": False}

    @app.get("/buy")
    def buy_page():
        return FileResponse(ROOT / "static" / "buy.html")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    return app


def app_factory() -> FastAPI:
    return create_app()
