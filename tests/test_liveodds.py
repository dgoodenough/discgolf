"""Live win-probability history: what gets recorded, what gets kept, and the
series the app draws from it.

The history is a plain CSV and the export is plain JSON, so these build the
inputs directly (a stand-in for the parts of SimResult liveodds reads) rather
than running a simulation — which lets each rule be exercised against a known
sequence of observations.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field

import pytest

from dgpt import liveodds, schedule


@dataclass
class FakeRes:
    """The three attributes liveodds.record reads off a SimResult."""
    pdga_numbers: list[int]
    names: list[str]
    live_stats: dict = field(default_factory=dict)


def stat(win, *, thru=18, rem=2.0, place=1, cur=-5.0):
    return {"win": win, "thru": thru, "rem": rem, "place": place, "cur": cur}


def res(stats: dict[int, dict], tid: int = 900003) -> FakeRes:
    pdga = sorted(stats)
    return FakeRes(pdga_numbers=pdga, names=[f"P{p}" for p in pdga],
                   live_stats={tid: stats})


def rows(path=None) -> list[dict]:
    with open(path or liveodds.HISTORY, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Pin liveodds._now to a ticking clock.

    Every record() here fires in the same millisecond, and taken_at is the
    block key — so without a seam a whole test's observations collapse into
    one. Production reads a real clock minutes apart (the live loop's own
    cadence); this just makes the ordering explicit rather than incidental.
    """
    ticks = iter(f"2026-08-29T{h:02d}:00:00.000+00:00" for h in range(8, 23))
    monkeypatch.setattr(liveodds, "_now", lambda: next(ticks))


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(liveodds, "HISTORY", tmp_path / "live_odds.csv")
    monkeypatch.setattr(liveodds, "OUT", tmp_path / "liveodds.json")
    monkeypatch.setattr(schedule, "load", lambda: [
        {"tournament_id": 900003, "name": "Test Live Open", "cls": "elite"},
        {"tournament_id": 900004, "name": "Test Next Open", "cls": "elite"},
    ])
    monkeypatch.setattr(schedule, "live_events",
                        lambda rows=None: [{"tournament_id": 900003}])


# ------------------------------------------------------------------ record

def test_no_live_event_records_nothing():
    assert "no live event" in liveodds.record(FakeRes([1], ["P1"]), "MPO")
    assert not liveodds.HISTORY.exists()


def test_records_contenders_and_the_leaderboard_only():
    r = res({
        1: stat(0.4, place=1),                                  # in it
        2: stat(0.0, place=3),                                  # top of the board
        3: stat(0.0, place=liveodds.RECORD_PLACE + 1),          # neither
        4: stat(0.002, place=None),                             # equity, no place yet
    })
    liveodds.record(r, "MPO")
    assert {int(x["pdga_number"]) for x in rows()} == {1, 2, 4}


def test_identical_block_is_skipped_but_a_changed_one_appends():
    r = res({1: stat(0.4), 2: stat(0.1)})
    liveodds.record(r, "MPO")
    assert "unchanged" in liveodds.record(r, "MPO")
    assert len(rows()) == 2

    r.live_stats[900003][1] = stat(0.55, thru=27)
    liveodds.record(r, "MPO")
    assert len({x["taken_at"] for x in rows()}) == 2


def test_divisions_do_not_shadow_each_other():
    r = res({1: stat(0.4)})
    liveodds.record(r, "MPO")
    liveodds.record(r, "FPO")     # same numbers, different division: still new
    assert {x["division"] for x in rows()} == {"MPO", "FPO"}


def test_only_the_newest_events_are_kept():
    for tid in (900001, 900002, 900003):
        liveodds.record(res({1: stat(0.4)}, tid=tid), "MPO")
    assert {x["tid"] for x in rows()} == {"900002", "900003"}
    assert liveodds.KEEP_EVENTS == 2


def test_retention_does_not_cut_across_divisions():
    """MPO plays an event FPO does not; FPO must keep its own newest two."""
    for tid in (900001, 900002):
        liveodds.record(res({1: stat(0.4)}, tid=tid), "FPO")
    for tid in (900003, 900004):
        liveodds.record(res({1: stat(0.4)}, tid=tid), "MPO")
    kept = {(x["division"], x["tid"]) for x in rows()}
    assert kept == {("FPO", "900001"), ("FPO", "900002"),
                    ("MPO", "900003"), ("MPO", "900004")}


# ------------------------------------------------------------------ export

def series_for(division: str) -> dict:
    liveodds.write_json()
    return json.loads(liveodds.OUT.read_text(encoding="utf-8"))[division]


def test_export_is_empty_without_history():
    assert series_for("mpo") is None
    assert series_for("fpo") is None


def test_x_axis_is_holes_played_and_never_walks_back():
    liveodds.record(res({1: stat(0.4, thru=18), 2: stat(0.3, thru=12)}), "MPO")
    liveodds.record(res({1: stat(0.5, thru=36), 2: stat(0.2, thru=30)}), "MPO")
    # the front of the field drops out of the recorded set entirely
    liveodds.record(res({2: stat(0.9, thru=33)}), "MPO")
    assert series_for("mpo")["x"] == [18, 36, 36]


def test_a_missing_player_reads_as_zero_not_as_a_gap():
    liveodds.record(res({1: stat(0.4), 2: stat(0.3)}), "MPO")
    liveodds.record(res({1: stat(0.99)}), "MPO")          # 2 fell out of the picture
    line = next(s for s in series_for("mpo")["series"] if s["pdga"] == 2)
    assert line["y"] == [0.3, 0.0]


def test_the_chart_keeps_a_fallen_leader_and_drops_the_never_theres():
    liveodds.record(res({
        1: stat(0.5), 2: stat(liveodds.PEAK_MIN + 0.1), 3: stat(0.0, place=4),
    }), "MPO")
    liveodds.record(res({
        1: stat(0.99), 2: stat(0.0, place=9), 3: stat(0.0, place=4),
    }), "MPO")
    charted = {s["pdga"] for s in series_for("mpo")["series"]}
    assert charted == {1, 2}          # 3 was never in it; 2 led and lost it


def test_the_line_cap_counts_the_contenders_it_left_off():
    n = liveodds.MAX_LINES + 4
    liveodds.record(res({p: stat(round(0.9 - p / 100, 4)) for p in range(1, n + 1)}), "MPO")
    out = series_for("mpo")
    assert len(out["series"]) == liveodds.MAX_LINES
    assert out["others"] == n - liveodds.MAX_LINES


def test_export_carries_the_event_and_the_holes_left():
    liveodds.record(res({1: stat(0.4, thru=36, rem=2.0)}), "MPO")
    out = series_for("mpo")
    assert out["event"] == "Test Live Open"
    assert out["tid"] == 900003 and out["live"] is True
    assert out["holes"] == 72          # 36 played + 2 rounds to go
    assert out["tracked_from"] == 36


def test_export_follows_the_newest_event_and_marks_a_finished_one():
    liveodds.record(res({1: stat(0.4)}, tid=900003), "MPO")
    liveodds.record(res({1: stat(0.6)}, tid=900004), "MPO")
    out = series_for("mpo")
    assert out["tid"] == 900004        # the newest event, not the live flag's
    assert out["live"] is False        # 900004 is over as far as the schedule knows


def test_the_move_column_covers_every_recorded_player():
    liveodds.record(res({1: stat(0.4), 2: stat(0.02, place=2)}), "MPO")
    liveodds.record(res({1: stat(0.5), 2: stat(0.01, place=2)}), "MPO")
    out = series_for("mpo")
    # 2 is below the chart's line but still in the table, so it needs a prior
    assert out["prev"] == {"1": 0.4, "2": 0.02}
