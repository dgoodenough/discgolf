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


def test_x_axis_is_the_fields_mean_progress():
    liveodds.record(res({1: stat(0.4, thru=18), 2: stat(0.3, thru=12)}), "MPO")
    liveodds.record(res({1: stat(0.5, thru=36), 2: stat(0.2, thru=30)}), "MPO")
    assert series_for("mpo")["x"] == [15, 33]


def test_x_axis_never_walks_back_when_the_recorded_set_changes():
    """The set shrinks as players fall out of contention; the clock cannot."""
    liveodds.record(res({1: stat(0.4, thru=18), 2: stat(0.3, thru=12)}), "MPO")
    liveodds.record(res({1: stat(0.5, thru=36), 2: stat(0.2, thru=30)}), "MPO")
    # the front of the field drops out of the recorded set entirely
    liveodds.record(res({2: stat(0.9, thru=33)}), "MPO")
    assert series_for("mpo")["x"] == [15, 33, 33]


def test_a_finished_front_card_does_not_stall_the_axis():
    """The regression this axis exists for (Idlewild R1, 2026-09-04).

    Reading the front of the field pins x the moment the first card is in:
    the max sat at 18 for six hours while the late cards played, collapsing
    an afternoon of the tournament onto one x and drawing the odds as a
    vertical line. Anchoring on the lead card inverts the same fault — it
    tees off last, so it pins through the morning wave instead.
    """
    # card 1 is done; the rest of the field plays on behind it
    liveodds.record(res({1: stat(0.4, thru=18), 2: stat(0.3, thru=9),
                         3: stat(0.2, thru=6)}), "MPO")
    liveodds.record(res({1: stat(0.4, thru=18), 2: stat(0.3, thru=14),
                         3: stat(0.2, thru=11)}), "MPO")
    liveodds.record(res({1: stat(0.4, thru=18), 2: stat(0.3, thru=18),
                         3: stat(0.2, thru=17)}), "MPO")
    x = series_for("mpo")["x"]
    assert x == [11, 14, 18]
    assert len(set(x)) == len(x), "every observation must land on its own x"


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


# --------------------------------------------------------------- fork line

def fork_for(division: str) -> dict:
    return series_for(division)["fork"]


def line_at(f: dict, cover: float, i: int = 0):
    return f["lines"][f["cover"].index(cover)][i]


def test_the_fork_line_is_the_winners_score_quantile():
    """Walk the board down from the leader until `cover` of the win
    probability is behind you; the score you stop on is the line."""
    liveodds.record(res({
        1: stat(0.60, cur=-12.0, place=1),     # 60% cumulative at -12
        2: stat(0.25, cur=-10.0, place=2),     # 85% by -10
        3: stat(0.12, cur=-8.0, place=3),      # 97% by -8
        4: stat(0.02, cur=-4.0, place=9),      # 99% by -4
        5: stat(0.01, cur=1.0, place=20),      # 100% by +1
    }), "MPO")
    f = fork_for("mpo")
    assert f["cover"] == [0.995, 0.95, 0.80]
    assert line_at(f, 0.80) == -10.0          # 60% is short, 85% clears
    assert line_at(f, 0.95) == -8.0           # 97% is the first past 95%
    assert line_at(f, 0.995) == 1.0           # only the whole board clears 99.5%
    assert f["lead"] == [-12.0]


def test_the_covered_set_is_downward_closed():
    """The quantile runs over SCORES, not over players ranked by odds.

    Taking the best players until they sum to `cover` and reading off the worst
    score among them would skip player 3 here — better score, less equity —
    and leave its mass above the line uncounted, which is exactly the amount
    the label claims is below it.
    """
    liveodds.record(res({
        1: stat(0.70, cur=-12.0, place=1),
        2: stat(0.20, cur=-6.0, place=8),      # more equity, worse score
        3: stat(0.10, cur=-9.0, place=4),      # less equity, better score
    }), "MPO")
    f = fork_for("mpo")
    # by score: -12 (70%), -9 (80%), -6 (100%). 80% is reached AT -9.
    assert line_at(f, 0.80) == -9.0
    assert line_at(f, 0.95) == -6.0


def test_players_tied_on_a_score_are_covered_together():
    """A cut lands on a score, so a tied group is in or out as a block."""
    liveodds.record(res({
        1: stat(0.50, cur=-10.0, place=1),
        2: stat(0.16, cur=-7.0, place=2),      # three players tied on -7,
        3: stat(0.16, cur=-7.0, place=2),      # 48% between them: only the
        4: stat(0.16, cur=-7.0, place=2),      # whole group clears 95%
        5: stat(0.02, cur=0.0, place=30),
    }), "MPO")
    f = fork_for("mpo")
    assert line_at(f, 0.80) == -7.0
    assert line_at(f, 0.95) == -7.0
    assert line_at(f, 0.995) == 0.0


def test_the_lines_cannot_cross():
    """A more cautious call always sits further down the board, and the app
    fills the gaps between the lines as bands — an inverted one would fold."""
    liveodds.record(res({p: stat(round(0.5 / p, 4), cur=float(p - 12), place=p)
                         for p in range(1, 26)}), "MPO")
    liveodds.record(res({p: stat(round(0.9 / p ** 2, 4), cur=float(p - 20), place=p)
                         for p in range(1, 26)}), "MPO")
    f = fork_for("mpo")
    for i in range(len(f["lines"][0])):
        col = [line[i] for line in f["lines"]]      # outermost first
        assert col == sorted(col, reverse=True), f"lines crossed at {i}: {col}"
        assert f["lead"][i] <= col[-1]


def test_rounding_in_the_recorded_odds_does_not_move_a_line():
    """`win` is rounded to 4dp per player, so a block's mass misses 1.0.

    Unnormalised, a block summing to 0.9993 can never reach 0.995 and every
    deep line would be dragged to the bottom of the board.
    """
    stats = {1: stat(0.9970, cur=-9.0, place=1), 2: stat(0.0023, cur=-2.0, place=6)}
    assert sum(s["win"] for s in stats.values()) < 1.0
    liveodds.record(res(stats), "MPO")
    assert line_at(fork_for("mpo"), 0.995) == -9.0


def test_the_fork_counts_the_players_still_in_it():
    liveodds.record(res({
        1: stat(0.80, cur=-9.0, place=1),
        2: stat(0.15, cur=-5.0, place=4),
        3: stat(0.05, cur=3.0, place=22),
    }), "MPO")
    f = fork_for("mpo")
    at = f["cover"].index(0.95)
    assert f["lines"][at][0] == -5.0
    assert f["alive"][at][0] == 2          # the -9 and the -5, not the +3


def test_the_named_holder_is_the_strongest_player_on_the_line():
    """The whole tied group is covered, so the name is the one worth printing."""
    liveodds.record(res({
        1: stat(0.70, cur=-9.0, place=1),
        2: stat(0.05, cur=-4.0, place=12),     # same score, the weaker of the two
        3: stat(0.25, cur=-4.0, place=11),     # same score, the one to name
    }), "MPO")
    f = fork_for("mpo")
    at = f["cover"].index(0.95)
    assert f["lines"][at][0] == -4.0
    assert f["names"][str(f["who"][at][0])] == "P3"


def test_the_fork_names_only_the_players_who_held_a_line():
    liveodds.record(res({
        1: stat(0.90, cur=-9.0, place=1),
        2: stat(0.10, cur=-4.0, place=12),
        3: stat(0.0, cur=3.0, place=20),       # recorded, but never on a line
    }), "MPO")
    assert set(fork_for("mpo")["names"].values()) == {"P1", "P2"}


# ------------------------------------------------------------ who fell out

def out_for(division: str) -> dict:
    return series_for(division)["out"]


def test_the_drop_off_is_the_last_crossing_not_the_first():
    """Players bounce across the floor — 23 to 35 of ~80 do it in a real race,
    one of them thirteen times — so the first dip is not the elimination."""
    for w in (0.40, 0.0005, 0.30, 0.0004, 0.0002):     # under, back, under for good
        liveodds.record(res({1: stat(w), 2: stat(0.5, place=2)}), "MPO")
    out = out_for("mpo")
    assert out["1"][0] == 3, "marked the first dip rather than the last"
    assert out["1"][1] == "P1"


def test_a_player_still_in_it_has_no_drop_off():
    liveodds.record(res({1: stat(0.40), 2: stat(0.30, place=2)}), "MPO")
    liveodds.record(res({1: stat(0.60), 2: stat(0.20, place=2)}), "MPO")
    assert out_for("mpo") == {}


def test_a_player_who_was_never_in_it_has_no_drop_off():
    """Recorded for their place on the board, never above the floor."""
    liveodds.record(res({1: stat(0.9), 2: stat(0.0002, place=3)}), "MPO")
    liveodds.record(res({1: stat(1.0), 2: stat(0.0, place=3)}), "MPO")
    assert "2" not in out_for("mpo")
    assert out_for("mpo") == {}


def test_falling_out_of_the_recorded_set_counts_as_dropping_off():
    """`_block` stops recording a player with no equity and no top-25 place;
    the gap is the elimination, not missing data."""
    liveodds.record(res({1: stat(0.5), 2: stat(0.5, place=2)}), "MPO")
    liveodds.record(res({1: stat(1.0)}), "MPO")        # 2 vanishes entirely
    assert out_for("mpo")["2"] == [1, "P2"]


def test_the_floor_is_the_tabs_own_one():
    assert liveodds.CHART_MIN == 0.001
    liveodds.record(res({1: stat(0.9), 2: stat(liveodds.CHART_MIN, place=2)}), "MPO")
    liveodds.record(res({1: stat(1.0), 2: stat(liveodds.CHART_MIN / 2, place=2)}), "MPO")
    # exactly at the floor still counts as alive; below it does not
    assert out_for("mpo")["2"] == [1, "P2"]
