"""Spend guard: daily caps on YouCam units and OpenRouter dollars, global and per IP.

Counters persist in private/guard.json keyed by UTC date. Enforced server-side
BEFORE any paid call. Limits come from env vars (read at construction).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

UNITS_PER_ANALYSIS = 12


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


class GuardLimit(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


class Guard:
    def __init__(self, path: str | Path, *, daily_units: float | None = None, ip_units: float | None = None,
                 ip_agent_runs: float | None = None, daily_usd: float | None = None):
        self.path = Path(path)
        self.daily_units = _env("SKINPROOF_DAILY_UNITS", 120) if daily_units is None else daily_units
        self.ip_units = _env("SKINPROOF_IP_UNITS", 72) if ip_units is None else ip_units
        self.ip_agent_runs = _env("SKINPROOF_IP_AGENT_RUNS", 3) if ip_agent_runs is None else ip_agent_runs
        self.daily_usd = _env("SKINPROOF_DAILY_USD", 1.0) if daily_usd is None else daily_usd
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _load(self) -> dict:
        d = {}
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            pass
        if d.get("date") != self._today():
            d = {"date": self._today(), "units": 0.0, "usd": 0.0, "ips": {}}
        return d

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        tmp.replace(self.path)

    @staticmethod
    def _ip(d: dict, ip: str) -> dict:
        return d["ips"].setdefault(ip, {"units": 0.0, "agent_runs": 0})

    def units_left(self) -> float:
        with self._lock:
            d = self._load()
            return max(0.0, self.daily_units - d["units"])

    def reserve_units(self, ip: str, n: float) -> None:
        with self._lock:
            d = self._load()
            u = self._ip(d, ip)
            if d["units"] + n > self.daily_units + 1e-9:
                raise GuardLimit("Today's shared live-analysis allowance on this demo is used up. "
                                 "It resets daily (UTC). The free sample and real-run replays still work.", "global")
            if u["units"] + n > self.ip_units + 1e-9:
                raise GuardLimit("You have reached the daily live-analysis limit for this demo. "
                                 "The free sample and real-run replays still work.", "ip")
            d["units"] += n
            u["units"] += n
            self._save(d)

    def refund_units(self, ip: str, n: float) -> None:
        if n <= 0:
            return
        with self._lock:
            d = self._load()
            u = self._ip(d, ip)
            d["units"] = max(0.0, d["units"] - n)
            u["units"] = max(0.0, u["units"] - n)
            self._save(d)

    def reserve_agent(self, ip: str, units: float) -> None:
        """Check the agent-run caps and reserve `units`; call settle_agent afterwards."""
        with self._lock:
            d = self._load()
            u = self._ip(d, ip)
            if u["agent_runs"] + 1 > self.ip_agent_runs:
                raise GuardLimit("You have reached the daily agent-run limit for this demo. "
                                 "The free sample and real-run replays still work.", "ip_runs")
            if d["usd"] >= self.daily_usd:
                raise GuardLimit("Today's shared AI allowance on this demo is used up. "
                                 "The free sample and real-run replays still work.", "usd")
            if d["units"] + units > self.daily_units + 1e-9:
                raise GuardLimit("Today's shared live-analysis allowance on this demo is used up. "
                                 "It resets daily (UTC). The free sample and real-run replays still work.", "global")
            if u["units"] + units > self.ip_units + 1e-9:
                raise GuardLimit("You have reached the daily live-analysis limit for this demo. "
                                 "The free sample and real-run replays still work.", "ip")
            d["units"] += units
            u["units"] += units
            u["agent_runs"] += 1
            self._save(d)

    def settle_agent(self, ip: str, reserved: float, spent: float, usd: float) -> None:
        with self._lock:
            d = self._load()
            u = self._ip(d, ip)
            diff = max(0.0, reserved - spent)
            d["units"] = max(0.0, d["units"] - diff)
            u["units"] = max(0.0, u["units"] - diff)
            d["usd"] += max(0.0, float(usd or 0))
            self._save(d)
