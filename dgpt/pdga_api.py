"""Authenticated client for the official PDGA REST API.

Docs: pdga.com/dev/api/rest/v1/auth and /services. Session-cookie auth;
sessions are cached to disk and refreshed on 401/403.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from . import config

BASE = "https://api.pdga.com"
UA = "dgpt-forecast/1.0 (github.com/dgoodenough/discgolf)"
SESSION_CACHE = config.CACHE_DIR / "pdga_session.json"


class PDGAClient:
    def __init__(self) -> None:
        env = config.load_env()
        self.username = env.get("PDGA_USERNAME")
        self.password = env.get("PDGA_PASSWORD")
        if not (self.username and self.password):
            raise RuntimeError("Set PDGA_USERNAME / PDGA_PASSWORD in .env (see .env.example)")
        self._cookie: str | None = None

    # -- session ---------------------------------------------------------
    def _login(self) -> None:
        body = json.dumps({"username": self.username, "password": self.password}).encode()
        req = urllib.request.Request(
            f"{BASE}/services/json/user/login",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": UA},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        self._cookie = f"{data['session_name']}={data['sessid']}"
        SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_CACHE.write_text(json.dumps({"cookie": self._cookie, "ts": time.time()}))

    def _ensure_session(self) -> str:
        if self._cookie:
            return self._cookie
        if SESSION_CACHE.exists():
            cached = json.loads(SESSION_CACHE.read_text())
            if time.time() - cached.get("ts", 0) < 20 * 3600:
                self._cookie = cached["cookie"]
                return self._cookie
        self._login()
        return self._cookie

    # -- requests --------------------------------------------------------
    def _get(self, path: str, **params: Any) -> dict:
        url = f"{BASE}/services/json/{path}?" + urllib.parse.urlencode(params)
        for attempt in (1, 2):
            req = urllib.request.Request(
                url, headers={"Cookie": self._ensure_session(), "User-Agent": UA}
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 1:
                    self._cookie = None
                    SESSION_CACHE.unlink(missing_ok=True)
                    continue
                raise
        raise RuntimeError("unreachable")

    def events(self, *, tier: str, start_date: str, end_date: str, **extra: Any) -> list[dict]:
        """All events for a tier within a date range (paginates past 200)."""
        out: list[dict] = []
        offset = 0
        while True:
            data = self._get(
                "event", tier=tier, start_date=start_date, end_date=end_date,
                limit=200, offset=offset, **extra,
            )
            batch = data.get("events") or []
            out.extend(batch)
            if len(batch) < 200:
                return out
            offset += 200

    def player(self, pdga_number: int) -> dict | None:
        data = self._get("players", pdga_number=pdga_number)
        players = data.get("players") or []
        return players[0] if players else None

    def player_statistics(self, *, year: int, division_code: str) -> list[dict]:
        """Season stats (incl. current rating) for every player in a division."""
        out: list[dict] = []
        offset = 0
        while True:
            data = self._get(
                "player-statistics", year=year, division_code=division_code,
                limit=200, offset=offset,
            )
            batch = data.get("players") or []
            out.extend(batch)
            if len(batch) < 200:
                return out
            offset += 200
