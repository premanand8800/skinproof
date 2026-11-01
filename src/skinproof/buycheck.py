"""'Before you buy': turn extracted claims into a claim audit and a verification plan. Deterministic after extraction."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

from .youcam import CONCERN_LABELS

CACHE_TTL = 24 * 3600
DEFAULT_WEEKS = 8
BISSETT = {"title": "Bissett et al. 2005: 5% niacinamide, randomised split-face trial, 12 weeks",
           "url": "https://pubmed.ncbi.nlm.nih.gov/16029679/", "weeks": 12}
SITOHANG = {"title": "Sitohang et al. 2022: topical tretinoin for photoaging, review of 7 RCTs of 3 to 24 months",
            "url": "https://pubmed.ncbi.nlm.nih.gov/35620028/", "weeks": 12}


def cache_key(text: str | None, url: str | None, image: bytes | None) -> str:
    if image:
        raw = b"img:" + image
    elif url:
        raw = b"url:" + url.strip().lower().encode()
    else:
        raw = b"txt:" + " ".join((text or "").split()).lower().encode()
    return hashlib.sha256(raw).hexdigest()[:40]


class Cache:
    def __init__(self, root: Path, ttl: int = CACHE_TTL):
        self.dir, self.ttl = Path(root) / "buycheck", ttl

    def get(self, key: str) -> dict | None:
        try:
            d = json.loads((self.dir / f"{key}.json").read_text())
        except Exception:
            return None
        return d["result"] if time.time() - d.get("at", 0) < self.ttl else None

    def put(self, key: str, result: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{key}.json").write_text(json.dumps({"at": time.time(), "result": result}))


def check_url(url: str) -> str:
    """Only public http(s) pages (server-side fetch must not reach private networks)."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Enter a full product link starting with http:// or https://")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except OSError:
        raise ValueError("Could not find that website.")
    for i in infos:
        ip = ipaddress.ip_address(i[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("That address is not a public website.")
    return url.strip()


def _trial_note(product: str, claims: list[dict], blob: str) -> dict:
    b = (product + " " + blob).lower()
    refs = []
    if "niacinamide" in b:
        refs.append(BISSETT)
    if "tretinoin" in b or "retinol" in b or "retinoid" in b:
        refs.append(SITOHANG)
    if not refs:
        refs = [BISSETT, SITOHANG]
    longest = max((r["weeks"] for r in refs), default=0)
    tfs = [c["timeframe_weeks"] for c in claims if c.get("concern") and c.get("timeframe_weeks")]
    short = bool(tfs) and min(tfs) < longest
    text = ("Clinical trials of skincare actives run for weeks, not days. "
            + (f"The shortest timeframe on this label ({min(tfs)} week{'s' if min(tfs) != 1 else ''}) is shorter than "
               f"the {longest}-week trial below, so a quick change may be real but is not what the trial tested. " if short else "")
            + "Trial length is a reason to keep checking, not proof the label is wrong.")
    return {"text": text, "refs": refs}


def build(extracted: dict) -> dict:
    claims = extracted.get("claims") or []
    out = []
    for c in claims:
        concern, tf = c.get("concern"), c.get("timeframe_weeks")
        item = {"quote": c["quote"], "concern": concern,
                "concern_label": CONCERN_LABELS.get(concern, concern) if concern else None,
                "timeframe_weeks": tf, "measurable": bool(concern)}
        if concern:
            n = tf or DEFAULT_WEEKS
            item["check_week"] = n
            item["plan"] = ("Take your 4-photo noise check today, then check in weekly with the same light. "
                            + (f"By week {n} SkinProof can tell you whether this claim holds on your face."
                               if tf else f"The label gives no timeframe, so plan {n} weeks of weekly check-ins; "
                               f"by week {n} SkinProof can tell you if anything beyond your noise happened."))
        else:
            item["check_week"] = None
            item["plan"] = "A photo cannot measure this, so SkinProof will not judge it. Treat it as marketing."
        out.append(item)
    meas = [i for i in out if i["measurable"]]
    weeks = [i["timeframe_weeks"] for i in meas if i["timeframe_weeks"]]
    blob = " ".join(i["quote"] for i in out)
    audit = {"measurable": len(meas), "marketing_only": len(out) - len(meas), "total": len(out),
             "earliest_week": min(weeks) if weeks else (DEFAULT_WEEKS if meas else None),
             "earliest_is_stated": bool(weeks)}
    return {"product": extracted.get("product") or "", "source": extracted.get("source"), "claims": out,
            "audit": audit, "trial": _trial_note(extracted.get("product") or "", out, blob),
            "cost": extracted.get("cost", 0.0)}
