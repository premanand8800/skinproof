"""Minimal MCP client for Perfect Corp's official YouCam MCP servers.

Servers: https://mcp-api-01.makeupar.com/mcp/{beauty,fashion,creators}, HTTP
transport, ``Authorization: Bearer <YOUCAM_API_KEY>``. Plain JSON-RPC 2.0 over
httpx (initialize -> notifications/initialized -> tools/list -> tools/call);
replies may be JSON or a single SSE event. The key is never logged.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from pathlib import Path

from .youcam import (DEFAULT_ACTIONS, YouCamError, cache_key, load_api_key, normalize_actions,
                      parse_result, preprocess_image)

MCP_BASE = "https://mcp-api-01.makeupar.com/mcp"
PROTOCOL = "2025-06-18"


class YouCamMCP:
    def __init__(self, server: str = "beauty", api_key: str | None = None, *,
                 base_url: str = MCP_BASE, http: httpx.Client | None = None, timeout: float = 120.0,
                 cache_dir: str | Path = ".cache/youcam_mcp", poll_interval: float = 2.0):
        self.cache_dir, self.poll_interval, self.timeout = Path(cache_dir), poll_interval, timeout
        self._key = api_key or load_api_key()
        self.url = f"{base_url.rstrip('/')}/{server}"
        self.http = http or httpx.Client(timeout=timeout)
        self._id = 0
        self._session: str | None = None
        self._ready = False

    def __repr__(self) -> str:
        return f"YouCamMCP(url={self.url!r}, has_key={bool(self._key)})"

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise YouCamError("NoApiKey", "YOUCAM_API_KEY is not configured on the server.")
        h = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL}
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    @staticmethod
    def _parse(r: httpx.Response) -> dict[str, Any]:
        text = r.text or ""
        if "text/event-stream" in r.headers.get("content-type", ""):
            msg = None
            for line in text.splitlines():
                if line.startswith("data:") and line[5:].strip():
                    try:
                        cand = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(cand, dict) and ("result" in cand or "error" in cand):
                        msg = cand
            return msg or {}
        try:
            return r.json() if text.strip() else {}
        except ValueError:
            return {}

    def _post(self, payload: dict[str, Any], *, expect_reply: bool = True) -> dict[str, Any]:
        r = self.http.post(self.url, headers=self._headers(), json=payload)
        if r.status_code >= 400:
            raise YouCamError(f"MCP_HTTP_{r.status_code}", f"YouCam MCP returned HTTP {r.status_code}", status=r.status_code)
        sid = r.headers.get("mcp-session-id")
        if sid:
            self._session = sid
        if not expect_reply:
            return {}
        msg = self._parse(r)
        if "error" in msg:
            e = msg["error"] or {}
            raise YouCamError(f"MCP_{e.get('code', 'error')}", str(e.get("message", "MCP error")))
        return msg.get("result") or {}

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        return self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})

    def initialize(self) -> dict[str, Any]:
        if self._ready:
            return {}
        res = self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                       "clientInfo": {"name": "skinproof", "version": "0.1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        self._ready = True
        return res

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            res = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools += res.get("tools", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a tool; returns the parsed JSON payload (structuredContent, else JSON text, else text)."""
        self.initialize()
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        content = res.get("content") or []
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        if res.get("isError"):
            raise YouCamError("MCP_ToolError", text[:300] or f"{name} failed")
        if res.get("structuredContent") is not None:
            return res["structuredContent"]
        try:
            return json.loads(text)
        except ValueError:
            return text

    # ---- skin analysis flow (same units as REST: the tools wrap the same API)
    def balance(self) -> float | None:
        res = _unwrap(self.call_tool("Get-Unit-Info")).get("results") or []
        return sum(float(x.get("amount_dec", x.get("amount", 0))) for x in res) if res else None

    def upload(self, jpeg: bytes, file_name: str = "selfie.jpg") -> str:
        out = self.call_tool("upload_file", {"task_type": "AI-Skin-Analysis", "files": [
            {"file_name": file_name, "content_type": "image/jpeg",
             "data": base64.b64encode(jpeg).decode()}]})
        files = out.get("result") if isinstance(out, dict) else out
        try:
            return files[0]["file_id"]
        except (TypeError, KeyError, IndexError):
            raise YouCamError("UploadFailed", "YouCam MCP upload returned no file_id")

    def _status(self, task_id: str) -> dict[str, Any]:
        return _unwrap(self.call_tool("Get-Running-Task-Status",
                                      {"task_id": task_id, "task_type": "AI-Skin-Analysis"}))

    def analyze(self, image_bytes: bytes, actions: list[str] | None = None,
                *, use_cache: bool = True) -> dict[str, Any]:
        """Same contract as YouCamClient.analyze, executed through YouCam's MCP tools."""
        actions = normalize_actions(actions or DEFAULT_ACTIONS)
        jpeg = preprocess_image(image_bytes)
        path = self.cache_dir / f"{cache_key(jpeg, actions)}.json"
        if use_cache and path.is_file():
            res = parse_result(json.loads(path.read_text()))
            res["cached"] = True
            return res
        file_id = self.upload(jpeg)
        out = _unwrap(self.call_tool("AI-Skin-Analysis", {"polling": True, "request": {
            "src_file_id": file_id, "dst_actions": actions, "format": "json",
            "miniserver_args": {"enable_mask_overlay": True}}}))
        d = out.get("data", out)
        deadline = time.monotonic() + self.timeout
        while d.get("task_status") not in ("success", "error"):
            if time.monotonic() > deadline or not d.get("task_id"):
                raise YouCamError("Timeout", "Analysis did not finish in time")
            time.sleep(self.poll_interval)
            out = self._status(d["task_id"])
            d = out.get("data", out)
        if d.get("task_status") == "error":
            raise YouCamError(str(d.get("error") or d.get("error_code") or "TaskError"),
                              str(d.get("error_message") or "Analysis failed"))
        raw = {"data": d}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))
        res = parse_result(raw)
        res["cached"] = False
        return res

    def download_bytes(self, url: str) -> bytes | None:
        r = self.http.get(url)
        return r.content if r.status_code == 200 else None


def _unwrap(x: Any) -> dict[str, Any]:
    """Tool payloads are sometimes wrapped as {"result": {...}}."""
    if isinstance(x, dict) and set(x) == {"result"} and isinstance(x["result"], dict):
        return x["result"]
    return x if isinstance(x, dict) else {}
