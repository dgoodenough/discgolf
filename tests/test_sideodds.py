"""Odds-only events (dgpt/sideodds.py): no points, live win odds only.

What has to hold: the rows find their event (by id, or by an unambiguous
name), the live gates see them, the simulation gives a sane race off real
in-progress scores, and none of it reaches the points schedule.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from dgpt import config, livecheck, liveodds, ratings, schedule, sideodds
from tests.conftest import event_payload, round_payload, row

TID = 97346
TODAY = dt.date.today()


def day(offset: int) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def live_event(name: str = "United States Disc Golf Championship", start: int = -1,
               end: int = 2, division: str = "MPO", latest: int = 2) -> dict:
    return {**event_payload(name, 4, [(division, latest)], end_date=day(end)),
            "StartDate": day(start)}


# Round 1 done, round 2 half played. 9001 leads by three; 9004 is unrated.
FIELD = [(9001, "Lead Er", 1040), (9002, "Chase Er", 1035),
         (9003, "Mid Field", 1020), (9004, "No Rating", None)]
R1 = {9001: -6, 9002: -3, 9003: -2, 9004: -1}
R2 = {9001: -1, 9002: -1, 9003: 0, 9004: 0}


def stage(fake_api, tid: int = TID, division: str = "MPO", **kw) -> None:
    fake_api.event(tid, live_event(division=division, **kw))
    places = {p: i for i, p in enumerate(sorted(R1, key=lambda p: R1[p] + R2[p]), 1)}
    fake_api.round(tid, division, 1, round_payload([
        row(p, n, r, round_to_par=R1[p], played=18, has_score=True) for p, n, r in FIELD]))
    fake_api.round(tid, division, 2, round_payload([
        row(p, n, r, round_to_par=R2[p], played=9, running_place=places[p]) for p, n, r in FIELD]))


@pytest.fixture
def side(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SIDE_EVENTS", (
        {"key": "usdgc", "tid": TID, "divisions": ("MPO",)},
        {"key": "throw-pink", "name": "Throw Pink", "divisions": ("FPO",)},
    ))
    monkeypatch.setattr(sideodds, "SIDE_CSV", tmp_path / "side_events.csv")
    monkeypatch.setattr(ratings, "current",
                        lambda division: {p: r for p, _, r in FIELD if r})
    monkeypatch.setattr(schedule, "load", lambda: [])
    monkeypatch.setattr(liveodds, "HISTORY", tmp_path / "live_odds.csv")
    monkeypatch.setattr(liveodds, "OUT", tmp_path / "liveodds.json")


def listing(tid: int, name: str, start: int = -1, end: int = 2) -> dict:
    return {"tournament_id": tid, "tournament_name": name,
            "start_date": day(start), "end_date": day(end)}


# ------------------------------------------------------------------- rows

def test_an_id_pinned_entry_builds_from_live_scoring(side, fake_api):
    stage(fake_api)
    rows = sideodds.build([], client=None)
    usdgc = next(r for r in rows if r["key"] == "usdgc")
    assert usdgc["tournament_id"] == TID
    assert usdgc["cls"] == sideodds.CLS and usdgc["fpo_points"] is False
    assert usdgc["mpo"] and not usdgc["fpo"]
    assert usdgc["completed"] is False
    assert [r["key"] for r in sideodds.load()] == ["usdgc"]   # Throw Pink not listed


def test_a_named_entry_is_found_in_the_listing(side, fake_api):
    stage(fake_api)
    stage(fake_api, tid=104001, division="FPO")
    rows = sideodds.build([listing(104001, "2026 Throw Pink Women's Championship")])
    assert {r["key"]: r["tournament_id"] for r in rows} == {"usdgc": TID, "throw-pink": 104001}
    # and once found, the id sticks: the next build needs no listing at all
    rows = sideodds.build([])
    assert {r["key"]: r["tournament_id"] for r in rows}["throw-pink"] == 104001


def test_a_named_entry_searches_the_extra_tiers(side, fake_api):
    stage(fake_api)
    stage(fake_api, tid=104001, division="FPO")

    class Client:
        tiers: list[str] = []

        def events(self, *, tier, start_date, end_date):
            self.tiers.append(tier)
            return [listing(104001, "Throw Pink Women's Run")] if tier == "XA" else []

    c = Client()
    rows = sideodds.build([], client=c)
    assert {r["key"] for r in rows} == {"usdgc", "throw-pink"}
    assert c.tiers == list(config.SIDE_SEARCH_TIERS)


def test_an_ambiguous_name_tracks_nothing_and_says_so(side, fake_api, capsys):
    stage(fake_api)
    rows = sideodds.build([listing(1, "Throw Pink Club Classic"),
                           listing(2, "Throw Pink Women's Run")])
    assert "throw-pink" not in {r["key"] for r in rows}
    out = capsys.readouterr().out
    assert "::warning::" in out and "1 Throw Pink Club Classic" in out


def test_a_failed_refresh_keeps_the_last_row(side, fake_api):
    stage(fake_api)
    sideodds.build([])
    fake_api.envelopes.clear()          # live scoring goes dark
    rows = sideodds.build([])
    assert [r["tournament_id"] for r in rows] == [TID]


def test_the_points_schedule_never_sees_them(side, fake_api, tmp_path, monkeypatch):
    class Client:
        def events(self, *, tier, start_date, end_date):
            return [listing(TID, "United States Disc Golf Championship")] if tier == "XM" else []

    monkeypatch.setattr(schedule, "SCHEDULE_CSV", tmp_path / "schedule.csv")
    monkeypatch.setattr(config, "SIDE_SEARCH_TIERS", ("XM",))
    stage(fake_api)
    assert schedule.build(Client()) == []
    assert [r["tournament_id"] for r in sideodds.load()] == [TID]


# ------------------------------------------------------------- live gates

def test_the_gate_fills_in_an_entry_nothing_has_built_yet(side, fake_api):
    """The cron's gate is the first thing to run once an entry is added —
    possibly mid-event — so it must not wait for a schedule build."""
    stage(fake_api)
    assert not sideodds.SIDE_CSV.exists()
    assert [r["tournament_id"] for r in sideodds.any_live()] == [TID]
    assert sideodds.SIDE_CSV.exists()


def test_an_unstaged_entry_is_simply_not_live(side, fake_api):
    assert sideodds.any_live() == []


def test_a_finished_event_is_not_live(side, fake_api):
    stage(fake_api, start=-6, end=-3)
    assert sideodds.any_live() == []


def test_livecheck_hashes_the_odds_only_scores(side, fake_api):
    stage(fake_api)
    sideodds.build([])
    before = livecheck.signature()
    R2[9002] = -3
    try:
        from dgpt import live_api
        live_api._memo.clear()
        stage(fake_api)
        assert livecheck.signature() != before
    finally:
        R2[9002] = -1


# ------------------------------------------------------------ simulation

def test_the_race_follows_the_board(side, fake_api):
    stage(fake_api)
    res = sideodds.run("MPO", sideodds.live_events(), n_sims=4000)
    stats = res.live_stats[TID]
    assert set(stats) == {p for p, _, _ in FIELD}
    assert abs(sum(s["win"] for s in stats.values()) - 1) < 0.01
    assert max(stats, key=lambda p: stats[p]["win"]) == 9001
    assert stats[9001]["thru"] == 27 and stats[9001]["cur"] == -7.0
    assert stats[9001]["place"] == 1
    # the unrated player is raced, not dropped, and is named
    assert stats[9004]["win"] >= 0 and res.unrated == {TID: ["No Rating"]}
    assert res.names[res.pdga_numbers.index(9001)] == "Lead Er"


def test_the_race_is_seeded(side, fake_api):
    stage(fake_api)
    live = sideodds.live_events()
    assert (sideodds.run("MPO", live, n_sims=2000).live_stats
            == sideodds.run("MPO", live, n_sims=2000).live_stats)


def test_a_division_the_event_does_not_play_is_skipped(side, fake_api):
    stage(fake_api)
    assert sideodds.run("FPO", sideodds.live_events(), n_sims=500).live_stats == {}


def test_record_lands_in_the_history_and_the_tab(side, fake_api):
    stage(fake_api)
    sideodds.build([])
    assert "recorded live odds" in sideodds.record("MPO", n_sims=2000)
    assert "unchanged" in sideodds.record("MPO", n_sims=2000)
    liveodds.write_json()
    out = json.loads(liveodds.OUT.read_text())["mpo"]
    assert out["tid"] == TID and out["live"] is True and out["side"] is True
    assert out["cls"] == sideodds.CLS
    assert out["event"] == "United States Disc Golf Championship"
    table = {t["pdga"]: t for t in out["table"]}
    assert table[9001]["place"] == 1 and table[9001]["cur"] == -7.0


def test_record_never_raises(side, fake_api, monkeypatch, capsys):
    stage(fake_api)
    sideodds.build([])

    def boom(*a, **k):
        raise RuntimeError("live scoring changed shape")

    monkeypatch.setattr(sideodds, "run", boom)
    assert "skipped" in sideodds.record("MPO")
    assert "::warning::MPO odds-only events skipped" in capsys.readouterr().out


def test_a_live_points_event_outranks_an_odds_only_one(side, fake_api, monkeypatch):
    """Throw Pink against the Powerball Cup: both recorded, the Cup on the tab."""
    stage(fake_api)
    sideodds.build([])
    cup = config.TID_CHAMPIONSHIP
    monkeypatch.setattr(schedule, "load", lambda: [
        {"tournament_id": cup, "name": "DGPT Powerball Cup", "cls": "championship"}])
    monkeypatch.setattr(schedule, "live_events",
                        lambda rows=None: [{"tournament_id": cup}] if rows is None
                        else [r for r in rows if r["tournament_id"] != cup and r["start_date"] <= day(0)])

    class Res:
        pdga_numbers, names = [9001], ["Lead Er"]
        live_stats = {cup: {9001: {"win": 0.5, "thru": 9, "rem": 3.5, "place": 1, "cur": -2.0}}}

    liveodds.record(Res(), "MPO")
    sideodds.record("MPO", n_sims=500)          # recorded later, so newest
    liveodds.write_json()
    out = json.loads(liveodds.OUT.read_text())["mpo"]
    assert out["tid"] == cup and "side" not in out
