"""Live end-to-end run on the AI-generated synthetic face through the real HTTP API.
Spends YouCam units (about 70) and OpenRouter dollars (cents). Usage: python scripts/e2e_live.py --yes-spend"""
import json, sys, datetime as dt
from pathlib import Path
import httpx
from skinproof import noise

BASE = "http://127.0.0.1:8100"
ROOT = Path(__file__).resolve().parents[1]
URL = "https://theordinary.com/en-us/niacinamide-10-zinc-1-oil-control-serum-100436.html"

def main():
    if "--yes-spend" not in sys.argv:
        sys.exit("This spends about 70 YouCam units. Re-run with --yes-spend.")
    face = (ROOT / "private" / "synthetic_face.jpg").read_bytes()
    pert = dict(noise.perturbations(face))
    c = httpx.Client(base_url=BASE, timeout=600)
    u0 = c.get("/api/status").json()["units"]
    pid = c.post("/api/profiles", json={"name": "E2E synthetic"}).json()["id"]
    files = [("files", (n + ".jpg", pert[n], "image/jpeg")) for n in ("brightness-15", "brightness+15", "zoom-95")]
    cal = c.post(f"/api/profiles/{pid}/calibrate", files=files)
    print("calibrate", cal.status_code)
    six = (dt.date.today() - dt.timedelta(weeks=6)).isoformat()
    data = {"name": "The Ordinary Niacinamide 10% + Zinc 1%", "start_date": six,
            "concerns": "pore,texture", "claim_url": URL}
    r = c.post(f"/api/profiles/{pid}/products", data=data)
    print("product", r.status_code, r.json().get("id"))
    r = c.post(f"/api/profiles/{pid}/checkins", files={"file": ("base.jpg", face, "image/jpeg")}, data={"when": six})
    print("baseline", r.status_code, r.json().get("counted"))
    events, run_id = [], None
    with c.stream("POST", f"/api/profiles/{pid}/agent",
                  files={"file": ("new.jpg", pert["rotate+3"], "image/jpeg")},
                  data={"when": dt.date.today().isoformat()}) as resp:
        run_id = resp.headers.get("X-Run-Id")
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                ev, buf = buf.split("\n\n", 1)
                if ev.startswith("data:"):
                    events.append(json.loads(ev[5:]))
    u1 = c.get("/api/status").json()["units"]
    out = ROOT / "private" / "runs"; out.mkdir(parents=True, exist_ok=True)
    (out / f"e2e_{run_id}_sse.json").write_text(json.dumps(events, indent=1))
    fin = [e for e in events if e["type"] == "final"][-1]
    print("run_id", run_id, "profile", pid)
    print("units", u0, "->", u1, "agent_spent", fin["data"].get("units_spent"), "llm$", fin["data"].get("llm_cost"))
    for e in events:
        print(e["step"], e["type"], e.get("tool", ""), "|", e["summary"][:110])
    print(json.dumps({k: fin["data"].get(k) for k in ("decision", "wait_weeks", "headline", "reasons", "fallback", "disagreement")}, indent=1))

main()
