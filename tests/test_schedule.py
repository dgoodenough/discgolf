"""The live-window rule: date-based while the event runs, plus a one-day
grace window after end_date that stays open only until a refresh banks the
event (completed=True). The grace day is the Sunday-overrun fix: US final
rounds finish after 00:00 UTC, and a hard date cutoff froze Ledgestone 2026
on the second-to-last hole overnight.
"""
from __future__ import annotations

import datetime as dt

from dgpt import schedule


def _row(start: dt.date, end: dt.date, completed: bool = False) -> dict:
    return {
        "tournament_id": 1,
        "name": "Test Open",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "completed": completed,
    }


TODAY = dt.date.today()
D = dt.timedelta


def test_in_window_event_is_live():
    rows = [_row(TODAY - D(days=2), TODAY)]
    assert schedule.live_events(rows) == rows


def test_banked_on_its_own_last_day_is_not_live():
    # an event finishes hours before its final day is over. Once the refresh
    # has banked it the page must stop calling it live — otherwise the "LIVE
    # now" banner stays up over a finished event and the freshness check holds
    # the header to its 25-minute in-play threshold with nothing left to come
    rows = [_row(TODAY - D(days=2), TODAY, completed=True)]
    assert schedule.live_events(rows) == []


def test_in_window_unbanked_event_is_still_live():
    # the same last day, before the banking refresh: very much still live
    rows = [_row(TODAY - D(days=2), TODAY, completed=False)]
    assert schedule.live_events(rows) == rows


def test_ended_yesterday_unbanked_stays_live_through_grace_day():
    # Sunday finish, Monday 00:xx UTC: no refresh has banked it yet
    rows = [_row(TODAY - D(days=3), TODAY - D(days=1), completed=False)]
    assert schedule.live_events(rows) == rows


def test_ended_yesterday_banked_is_not_live():
    # the banking refresh committed completed=True: the loop should exit
    rows = [_row(TODAY - D(days=3), TODAY - D(days=1), completed=True)]
    assert schedule.live_events(rows) == []


def test_grace_window_is_one_day_only():
    # even unbanked, an event two days past its end date is not live —
    # the Monday full-refresh cron is the backstop, not the live loop
    rows = [_row(TODAY - D(days=4), TODAY - D(days=2), completed=False)]
    assert schedule.live_events(rows) == []


def test_future_event_is_not_live():
    rows = [_row(TODAY + D(days=3), TODAY + D(days=5))]
    assert schedule.live_events(rows) == []


# --- _row banking during the grace night ---------------------------------
# Past the end date the calendar says "bank it", but a US Sunday finish can
# still be on the course after 00:00 UTC — and final_results caches the
# sheet permanently once banked. Overnight, the scoreboard gets the veto.

def _event(start: dt.date, end: dt.date) -> dict:
    return {
        "tournament_id": 42,
        "tournament_name": "Test Open",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def _make_row(monkeypatch, *, hour: int, complete) -> dict:
    monkeypatch.setattr(
        schedule, "_utc_now",
        lambda: dt.datetime.combine(TODAY, dt.time(hour), dt.timezone.utc))
    calls = []

    def fake_complete(tid):
        calls.append(tid)
        if isinstance(complete, Exception):
            raise complete
        return complete

    monkeypatch.setattr(schedule.live_api, "event_complete", fake_complete)
    row = schedule._row(_event(TODAY - D(days=3), TODAY - D(days=1)), "elite",
                        mpo=True, fpo=True, fpo_points=True)
    row["_consulted"] = bool(calls)
    return row


def test_grace_night_holds_banking_while_play_continues(monkeypatch):
    row = _make_row(monkeypatch, hour=1, complete=False)
    assert row["completed"] is False


def test_grace_night_banks_once_scoreboard_confirms(monkeypatch):
    row = _make_row(monkeypatch, hour=1, complete=True)
    assert row["completed"] is True


def test_grace_night_falls_back_to_date_when_scoreboard_errors(monkeypatch):
    row = _make_row(monkeypatch, hour=1, complete=RuntimeError("api down"))
    assert row["completed"] is True


def test_grace_morning_banks_on_date_without_consulting(monkeypatch):
    # after 06:00 UTC even a west-coast night finish is done; a stuck
    # mid-round DNF row must not be able to stall banking past the cron
    row = _make_row(monkeypatch, hour=9, complete=False)
    assert row["completed"] is True
    assert row["_consulted"] is False
