"""Current official player ratings (PDGA publishes an update roughly monthly).

Event results carry the rating a player HELD when they played, so a standings
table built purely from results goes stale the moment PDGA publishes a new
ratings batch — a player's model rating wouldn't move until their next start.
This module keeps data/current_ratings.json in sync with the official
player-statistics endpoint and lets the rest of the pipeline overlay it:

- standings.compute() prefers these ratings over last-event ratings
- simulate.run() applies them to roster-added first-timers too
- livecheck folds the file's content hash into its change signature, so the
  monthly update triggers a re-simulation like any live-score change would

Fetches are throttled via a stamp in the (gitignored) cache dir, so the
15-minute cron stays cheap: at most one API sweep per TTL, and the committed
JSON is rewritten only when some rating actually changed. Everything degrades
gracefully without API credentials (validate in CI, local runs): the committed
snapshot keeps serving and the fetch is skipped with a note.
"""
from __future__ import annotations

import hashlib
import json
import time

from . import config

RATINGS_FILE = config.DATA_DIR / "current_ratings.json"
FETCH_STAMP = config.CACHE_DIR / "ratings_fetch_stamp"
TTL_SECONDS = 6 * 3600   # ratings move ~monthly; check a few times a day
MIN_PLAYERS = 50         # fewer than this in a division = partial response, keep old

_memo: dict | None = None  # one load/fetch decision per process


def _extract(rec: dict) -> tuple[int, int] | None:
    """(pdga_number, rating) from a player-statistics record, defensively."""
    pdga = rec.get("pdga_number") or rec.get("PDGANum") or rec.get("pdga_num")
    rating = rec.get("rating") or rec.get("player_rating") or rec.get("Rating")
    try:
        pdga, rating = int(pdga), int(float(rating))
    except (TypeError, ValueError):
        return None
    return (pdga, rating) if pdga > 0 and rating > 0 else None


def _load_file() -> dict:
    if RATINGS_FILE.exists():
        try:
            return json.loads(RATINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _stamp_age() -> float:
    try:
        return time.time() - float(FETCH_STAMP.read_text().strip())
    except (OSError, ValueError):
        return float("inf")


def refresh_if_stale() -> dict:
    """Return {"MPO": {pdga: rating}, "FPO": ...}, refetching past the TTL.

    Writes RATINGS_FILE only when a rating actually changed, so the committed
    snapshot (and livecheck's signature over it) is quiet between updates.
    """
    global _memo
    if _memo is not None:
        return _memo
    data = _load_file()
    if _stamp_age() < TTL_SECONDS:
        _memo = data
        return data
    try:
        from .pdga_api import PDGAClient

        client = PDGAClient()
        fresh: dict = {}
        for div in ("MPO", "FPO"):
            recs = client.player_statistics(year=config.SEASON, division_code=div)
            m: dict[str, int] = {}
            for rec in recs:
                kv = _extract(rec)
                if kv:
                    m[str(kv[0])] = kv[1]
            if len(m) < MIN_PLAYERS:
                raise RuntimeError(f"{div}: only {len(m)} rated players parsed — keeping previous snapshot")
            fresh[div] = m
        FETCH_STAMP.parent.mkdir(parents=True, exist_ok=True)
        FETCH_STAMP.write_text(str(time.time()), encoding="utf-8")
        if fresh != {k: data.get(k) for k in fresh}:
            RATINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            RATINGS_FILE.write_text(json.dumps(fresh, separators=(",", ":"), sort_keys=True), encoding="utf-8")
            data = fresh
    except Exception as e:  # no creds / API down / partial payload: serve the snapshot
        print(f"  current-ratings fetch skipped ({e})")
    _memo = data
    return data


def current(division: str) -> dict[int, int]:
    """{pdga_number: current official rating} for a division ({} if unknown)."""
    data = refresh_if_stale()
    return {int(k): int(v) for k, v in (data.get(division) or {}).items()}


def signature_component() -> str:
    """Hash of the ratings snapshot for livecheck's change signature."""
    data = refresh_if_stale()
    payload = json.dumps({k: data[k] for k in ("MPO", "FPO") if k in data}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()
