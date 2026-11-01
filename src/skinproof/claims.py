"""Extract product efficacy claims and compare them with measured verdicts. compare() is deterministic."""
from __future__ import annotations

import re
from typing import Any

import httpx

from . import llm
from .youcam import CONCERN_LABELS

MEASURABLE = tuple(k for k in CONCERN_LABELS if k != "skin_type")
MAX_CHARS = 15000

SUPPORTED, NOT_YET, CONTRADICTED, TOO_EARLY, NOT_MEASURABLE = (
    "SUPPORTED", "NOT_YET_SHOWN", "CONTRADICTED", "TOO_EARLY", "NOT_MEASURABLE")

SYSTEM = f"""You extract skincare product efficacy claims. Reply with JSON only:
{{"product": "<product name or empty>", "claims": [{{"concern": <key or null>, "quote": "<exact words from the source>", "timeframe_weeks": <integer or null>}}]}}
concern must be one of {list(MEASURABLE)} when the claim promises a visible improvement of that skin trait (e.g. smaller pores -> pore, hydration -> moisture, brighter/glow -> radiance, fewer breakouts -> acne, calmer skin -> redness, dark spots -> age_spot, fine lines -> wrinkle, smoother -> texture, plumper/lifted -> firmness, shine control -> oiliness). Use null if not measurable by photo (scent, 'dermatologist tested', 'clean ingredients', gentle, vegan, etc.).
timeframe_weeks: stated time to result converted to weeks (e.g. '4 weeks' -> 4, '28 days' -> 4, 'in 2 months' -> 8), else null. Only claims actually present in the source. Quote exactly. Max 12 claims."""


def _meta(html: str) -> str:
    import html as h
    parts = []
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if m:
        parts.append(m.group(1))
    for name in ("description", "og:title", "og:description"):
        for mm in re.finditer(r'(?is)<meta[^>]+(?:name|property)=["\']%s["\'][^>]*>' % re.escape(name), html):
            c = re.search(r'(?is)content=["\'](.*?)["\']', mm.group(0))
            if c:
                parts.append(c.group(1))
    for mm in re.finditer(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html):
        for d in re.finditer(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', mm.group(1)):
            parts.append(d.group(1).encode().decode("unicode_escape", "ignore") if "\\u" in d.group(1) else d.group(1))
    return "\n".join(dict.fromkeys(h.unescape(p).strip() for p in parts if p.strip()))


def _html_to_text(html: str) -> str:
    meta = _meta(html)
    mm = re.search(r"(?is)<main[^>]*>(.*)</main>", html)
    if mm:
        html = mm.group(1)
    body = _strip(html)
    return (meta + "\n" + body).strip() if meta else body


def _strip(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?i)</?(p|div|br|li|h[1-6]|tr|section)[^>]*>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as h
    text = h.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    # drop nav/menu boilerplate: keep title-like lines and sentence-like lines, dedupe
    seen, keep = set(), []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln or ln in seen or len(ln) < 20 or ln.startswith(("{", "}", "var ", "function")):
            continue
        seen.add(ln)
        keep.append(ln)
    return "\n".join(keep)


def fetch_url_text(url: str, client: httpx.Client | None = None) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SkinProof/0.1)", "Accept": "text/html,*/*"}
    c = client or httpx
    r = c.get(url, headers=headers, timeout=10.0, follow_redirects=True)
    r.raise_for_status()
    return _html_to_text(r.text)[:MAX_CHARS]


def _weeks(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 104 else None


def validate_claims(raw: Any) -> list[dict]:
    """Keep only well-formed claims; unknown concern -> null; drop empty quotes and extra fields."""
    items = raw.get("claims") if isinstance(raw, dict) else raw
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        quote = it.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            continue
        quote = quote.strip()
        concern = it.get("concern")
        if concern not in MEASURABLE:
            concern = None
        key = (concern, quote.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"concern": concern, "direction": "improve",
                    "timeframe_weeks": _weeks(it.get("timeframe_weeks")), "quote": quote})
    return out


def extract_claims(text: str | None = None, url: str | None = None, image: bytes | None = None,
                   *, client: httpx.Client | None = None, mime: str = "image/jpeg") -> dict:
    if image:
        source = "image"
        content: Any = [{"type": "text", "text": "Extract the product name and efficacy claims from this product label/box photo."},
                        llm.image_part(image, mime)]
    elif url:
        source = "url"
        page = fetch_url_text(url)
        content = f"Source: product page {url}\n\n{page}"
    elif text and text.strip():
        source = "text"
        content = text.strip()[:MAX_CHARS]
    else:
        raise ValueError("provide text, url or image")
    res = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                   max_tokens=6000, temperature=0.0, client=client,
                   response_format={"type": "json_object"})
    parsed = llm.parse_json(res["message"].get("content"))
    product = parsed.get("product") if isinstance(parsed, dict) else ""
    return {"product": product.strip() if isinstance(product, str) else "",
            "claims": validate_claims(parsed), "source": source,
            "cost": res["cost"], "model": res["model"]}


def _concerns(verdicts: Any) -> dict[str, dict]:
    if isinstance(verdicts, dict):
        verdicts = verdicts.get("concerns", [])
    return {v["concern"]: v for v in verdicts or [] if isinstance(v, dict) and "concern" in v}


def _num(x: float) -> str:
    return f"{x:+.0f}" if round(x) else "0"


def _line(c: dict, status: str, m: dict | None, weeks: float | None) -> str:
    q, tf = c["quote"], c.get("timeframe_weeks")
    head = f'Claim: "{q}".'
    if status == NOT_MEASURABLE:
        return f"{head} A photo cannot measure this, so SkinProof does not judge it."
    if status == TOO_EARLY:
        return f"{head} The claim needs {tf} weeks and you are at {weeks:g}. Too early to judge."
    lead = f"After {weeks:g} week{'s' if weeks != 1 else ''}, " if weeks is not None else ""
    if m is None:
        body = "there is no measurement for this concern yet: not yet shown."
    else:
        name = CONCERN_LABELS.get(c["concern"], c["concern"]).lower()
        thr, d = round(m["threshold"]), _num(m["delta"])
        if status == SUPPORTED:
            body = f"your {name} improved by {d}, beyond your {thr}-point noise: supported."
        elif status == CONTRADICTED:
            body = f"your {name} got worse by {d}, beyond your {thr}-point noise: contradicted."
        else:
            body = f"your {name} changed by {d}, inside your {thr}-point noise: not yet shown."
    if not lead:
        body = body[0].upper() + body[1:]
    return f"{head} {lead}{body}"


def compare(claims: list[dict], verdicts: Any, weeks_elapsed: float | None = None) -> list[dict]:
    by = _concerns(verdicts)
    if weeks_elapsed is None and isinstance(verdicts, dict):
        weeks_elapsed = verdicts.get("weeks_on_product")
    out = []
    for c in claims:
        concern, tf = c.get("concern"), c.get("timeframe_weeks")
        m = by.get(concern) if concern else None
        if not concern:
            status = NOT_MEASURABLE
        elif m and m.get("verdict") == "IMPROVED":
            status = SUPPORTED
        elif m and m.get("verdict") == "WORSE":
            status = CONTRADICTED
        elif tf and weeks_elapsed is not None and weeks_elapsed < tf:
            status = TOO_EARLY
        else:
            status = NOT_YET
        out.append({"concern": concern, "quote": c["quote"], "timeframe_weeks": tf,
                    "weeks_elapsed": weeks_elapsed, "measured": m, "status": status,
                    "sentence": _line(c, status, m, weeks_elapsed)})
    return out
