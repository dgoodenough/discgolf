"""Results fetcher using PDGA's public live-scoring API (no auth required).

live_results_fetch_event gives divisions + final round number; the final
round's scores carry RunningPlace = finishing place. Requests are throttled
and completed-event responses are cached to disk since they never change.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from . import config

BASE = "https://www.pdga.com/apps/tournament/live-api"
UA = {"User-Agent": "dgpt-forecast/1.0 (github.com/dgoodenough/discgolf)"}
LIVE_CACHE = config.CACHE_DIR / "live"
RESULTS_CACHE = config.CACHE_DIR / "results"

_MIN_INTERVAL = 0.5  # be polite: max ~2 req/s
_last_request = 0.0


def _get(url: str, cache_file: Path | None = None) -> dict:
    global _last_request
    if cache_file and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    for backoff in (0, 5, 15, 45):
        if backoff:
            time.sleep(backoff)
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                continue
            raise
    else:
        raise RuntimeError(f"still rate-limited after retries: {url}")
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def fetch_event(tournament_id: int, *, cache: bool = False) -> dict:
    cf = LIVE_CACHE / f"event_{tournament_id}.json" if cache else None
    return _get(f"{BASE}/live_results_fetch_event?TournID={tournament_id}", cf)["data"]


def fetch_round(tournament_id: int, division: str, round_num: int, *, cache: bool = False) -> dict:
    cf = LIVE_CACHE / f"round_{tournament_id}_{division}_{round_num}.json" if cache else None
    url = f"{BASE}/live_results_fetch_round?TournID={tournament_id}&Division={division}&Round={round_num}"
    return _get(url, cf)["data"]


def final_results(tournament_id: int, division: str, *, use_cache: bool = True) -> list[dict]:
    """Finishing order for a completed event.

    Returns [{pdga_number, name, rating, place, round_played}] sorted by
    place. DNF/WD players (no posted score in some regular round) are
    excluded — DGPT awards standings points to finishers only.
    """
    RESULTS_CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = RESULTS_CACHE / f"{tournament_id}_{division}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    event = fetch_event(tournament_id)
    end = event.get("EndDate")
    completed = bool(end) and date.fromisoformat(end) < date.today()

    div = next((d for d in event["Divisions"] if d["Division"] == division), None)
    if div is None:
        return []
    final_round = div.get("LatestRound") or event.get("FinalRound")
    scores = fetch_round(tournament_id, division, final_round, cache=completed).get("scores") or []

    # DNF detection: DGPT events have no cut in regular rounds (1..N; finals
    # use round ids 11/12), so a finisher must post a score in every regular
    # round. Withdrawn players keep a RunningPlace in the live data but earn
    # no standings points.
    finished: set[int] | None = None
    for rnum in range(1, 11):
        if rnum == final_round:
            break
        try:
            rd_scores = fetch_round(tournament_id, division, rnum, cache=completed).get("scores") or []
        except urllib.error.HTTPError as e:
            if e.code == 404:  # past the last regular round
                break
            raise
        if not rd_scores:
            break
        posted = {s["PDGANum"] for s in rd_scores if s.get("HasRoundScore")}
        finished = posted if finished is None else finished & posted
    if final_round <= 10:  # no finals: the last regular round counts too
        posted = {s["PDGANum"] for s in scores if s.get("HasRoundScore")}
        finished = posted if finished is None else finished & posted

    out = []
    for s in scores:
        if not s.get("RunningPlace"):
            continue
        if finished is not None and s.get("PDGANum") not in finished:
            continue
        # 999 = withdrew after qualifying for the finals (still "placed" in
        # live data, but a DNF officially)
        if str(s.get("GrandTotal")) == "999":
            continue
        out.append(
            {
                "pdga_number": s.get("PDGANum"),
                "name": s.get("Name"),
                "rating": s.get("Rating"),
                "place": s.get("RunningPlace"),
                "round_played": final_round,
            }
        )
    out.sort(key=lambda x: x["place"])

    if completed:  # in-progress results still change; don't freeze them
        cache_file.write_text(json.dumps(out), encoding="utf-8")
    return out
