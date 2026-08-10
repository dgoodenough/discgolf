"""livecheck is the live loop's gate: its signature must move when a live
score moves and hold still when nothing changed — a wrong 'unchanged' here is
exactly the silent-stale failure mode.

Each production check runs in a fresh process, so the per-process fetch memo
is cleared between signature() calls to match.
"""
from __future__ import annotations

import csv
import datetime as dt

from dgpt import live_api, livecheck, schedule


def _reschedule(tid: int, *, start_days_ago: int, end_days_ago: int, completed: bool) -> None:
    """Move one event's window in the schedule CSV the gate reads."""
    rows = schedule.load()
    today = dt.date.today()
    for r in rows:
        if r["tournament_id"] == tid:
            r["start_date"] = str(today - dt.timedelta(days=start_days_ago))
            r["end_date"] = str(today - dt.timedelta(days=end_days_ago))
            r["completed"] = completed
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        w.writerows(rows)


def _bump_a_live_score(fake_api, tid: int) -> None:
    url = next(u for u in fake_api.envelopes if f"TournID={tid}" in u and "Round=2" in u)
    sheet = fake_api.envelopes[url]["data"]["scores"]
    next(s for s in sheet if s["RoundtoPar"] is not None)["RoundtoPar"] -= 1
    live_api._memo.clear()


def test_signature_stable_when_nothing_changes(tiny_world):
    before = livecheck.signature()
    live_api._memo.clear()
    assert livecheck.signature() == before


def test_signature_moves_when_a_live_score_moves(tiny_world, fake_api):
    before = livecheck.signature()
    url = next(u for u in fake_api.envelopes
               if f"TournID={tiny_world.live_tid}" in u and "Round=2" in u)
    sheet = fake_api.envelopes[url]["data"]["scores"]
    scored = next(s for s in sheet if s["RoundtoPar"] is not None)
    scored["RoundtoPar"] -= 1
    live_api._memo.clear()
    assert livecheck.signature() != before


def test_signature_still_watches_an_event_in_its_grace_day(tiny_world, fake_api):
    """A US Sunday final round runs past 00:00 UTC, so an event whose end date
    is already yesterday can still be on the course. schedule.live_events()
    keeps it live until a refresh banks it, and the gate must keep hashing its
    scores for that whole window.

    Deriving `start <= today <= end` here instead stopped watching at midnight:
    every later check reported "unchanged", nothing re-simulated, and the site
    sat frozen with the lead card mid-round (Discmania Challenge, 2026-08-10)."""
    _reschedule(tiny_world.live_tid, start_days_ago=3, end_days_ago=1, completed=False)
    live_api._memo.clear()
    before = livecheck.signature()

    _bump_a_live_score(fake_api, tiny_world.live_tid)
    assert livecheck.signature() != before


def test_signature_stops_watching_once_the_event_banks(tiny_world, fake_api):
    """The other end of the grace window: once the refresh marks it completed
    the gate lets go, so a finished event's sheets can't keep waking the loop."""
    _reschedule(tiny_world.live_tid, start_days_ago=3, end_days_ago=1, completed=True)
    live_api._memo.clear()
    before = livecheck.signature()

    _bump_a_live_score(fake_api, tiny_world.live_tid)
    assert livecheck.signature() == before


def test_signature_ignores_non_live_noise(tiny_world, fake_api):
    """A completed event's sheets aren't refetched — editing them must not
    perturb the signature (the gate would otherwise re-simulate constantly)."""
    before = livecheck.signature()
    url = next(u for u in fake_api.envelopes
               if f"TournID={tiny_world.completed_tid}" in u and "Round=3" in u)
    fake_api.envelopes[url]["data"]["scores"][0]["RoundtoPar"] = -99
    live_api._memo.clear()
    assert livecheck.signature() == before
