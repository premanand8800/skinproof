"""Tiny JSON-file store. Photos live under private/ (gitignored)."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, root: str | Path = "private"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "db.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if self.db_path.is_file():
            return json.loads(self.db_path.read_text())
        return {"profiles": {}}

    def _save(self, db: dict) -> None:
        tmp = self.db_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(db, indent=1))
        tmp.replace(self.db_path)

    def create_profile(self, name: str) -> dict:
        with self._lock:
            db = self._load()
            pid = uuid.uuid4().hex[:10]
            p = {"id": pid, "name": name, "calibration": None, "products": [], "checkins": []}
            db["profiles"][pid] = p
            self._save(db)
            return p

    def get_profile(self, pid: str) -> dict | None:
        return self._load()["profiles"].get(pid)

    def list_profiles(self) -> list[dict]:
        return list(self._load()["profiles"].values())

    def _mutate(self, pid: str, fn) -> Any:
        with self._lock:
            db = self._load()
            p = db["profiles"].get(pid)
            if p is None:
                raise KeyError(pid)
            res = fn(p)
            self._save(db)
            return res

    def set_calibration(self, pid: str, cal: dict) -> None:
        self._mutate(pid, lambda p: p.__setitem__("calibration", cal))

    def add_product(self, pid: str, name: str, start_date: str, concerns: list[str]) -> dict:
        prod = {"id": uuid.uuid4().hex[:8], "name": name, "start_date": start_date, "concerns": concerns}
        self._mutate(pid, lambda p: p["products"].append(prod))
        return prod

    def add_checkin(self, pid: str, checkin: dict) -> dict:
        checkin = {"id": uuid.uuid4().hex[:8], "seq": time.time_ns(), **checkin}
        self._mutate(pid, lambda p: p["checkins"].append(checkin))
        return checkin

    def save_photo(self, pid: str, kind: str, data: bytes) -> str:
        """Write a (jpeg) photo under private/<pid>/ and return the relative path."""
        if not re.fullmatch(r"[a-z0-9_-]+", kind):
            raise ValueError("bad kind")
        d = self.root / pid
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{kind}-{uuid.uuid4().hex[:8]}.jpg"
        path.write_bytes(data)
        return str(path.relative_to(self.root))
