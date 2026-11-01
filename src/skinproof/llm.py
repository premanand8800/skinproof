"""Minimal OpenRouter chat client (sync, httpx). Never logs the API key."""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.8-flash"
FALLBACK_MODEL = "google/gemini-3.7-flash"
TIMEOUT = 90.0


class LLMError(Exception):
    pass


def load_api_key() -> str | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    for p in (Path(".env"), Path(__file__).resolve().parents[2] / ".env"):
        if p.is_file():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"") or None
    return None


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def parse_json(text: str | None) -> Any:
    """Parse JSON from a model reply, tolerating code fences and surrounding prose."""
    if not text:
        raise ValueError("empty reply")
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S | re.I)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in reply")
    i = min(starts)
    close = "}" if s[i] == "{" else "]"
    j = s.rfind(close)
    if j <= i:
        raise ValueError("no JSON found in reply")
    try:
        return json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON in reply: {e}") from e


def _post(payload: dict, key: str, retries: int, client: httpx.Client | None) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Title": "SkinProof"}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if client is not None:
                r = client.post(URL, json=payload, headers=headers, timeout=TIMEOUT)
            else:
                r = httpx.post(URL, json=payload, headers=headers, timeout=TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
        else:
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:
                last = LLMError(f"HTTP {r.status_code}")
                last.status = r.status_code  # type: ignore[attr-defined]
            else:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        if attempt < retries:
            time.sleep(min(8, 1.5 * 2 ** attempt) * _SLEEP_SCALE)
    raise last if last else LLMError("request failed")


_SLEEP_SCALE = 1.0


def chat(messages: list[dict], tools: list[dict] | None = None, model: str | None = None,
         max_tokens: int = 4000, temperature: float = 0.2,
         response_format: dict | None = None, retries: int = 2,
         client: httpx.Client | None = None) -> dict:
    key = load_api_key()
    if not key:
        raise LLMError("OPENROUTER_API_KEY is not set")
    model = model or DEFAULT_MODEL
    payload: dict[str, Any] = {"model": model, "messages": messages,
                               "max_tokens": max_tokens, "temperature": temperature}
    if tools:
        payload["tools"] = tools
    if response_format:
        payload["response_format"] = response_format
    try:
        data = _post(payload, key, retries, client)
    except (httpx.HTTPError, LLMError) as e:
        if model == FALLBACK_MODEL or (isinstance(e, LLMError) and not hasattr(e, "status")):
            raise
        payload["model"] = model = FALLBACK_MODEL
        data = _post(payload, key, 1, client)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"malformed response: {str(data)[:200]}") from e
    usage = data.get("usage") or {}
    return {"message": msg, "usage": usage, "cost": float(usage.get("cost") or 0.0),
            "model": data.get("model") or model}
