"""The "if it ended now" scenario: counted holes, and the standings they settle.

A weather-shortened event is not scored off the live leaderboard. A round in
progress counts only on the holes every player in it has completed (a 2026
DGPT lightning stop was scored exactly that way), so these tests pin the
intersection rule, its degenerate cases, and the season table built on it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from dgpt import asis, config, live_api, points
from .conftest import event_payload, round_payload, row

PARS = [3] * 18


def holes_row(pdga: int, name: str, strokes: list[int | None], *, tee_time="08:00",
              rating: int = 1000) -> dict:
    """A round-sheet row with hole-by-hole scores (None = not played yet)."""
    played = [s for s in strokes if s is not None]
    done = len(played) == 18
    r = row(pdga, name, rating, round_to_par=sum(played) - 3 * len(played) if played else None,
            played=len(played), has_score=done, tee_time=tee_time)
    r.update(
        HoleScores=["" if s is None else str(s) for s in strokes],
        Pars=",".join(str(p) for p in PARS),
        Holes=18, Completed=1 if done else 0,
    )
    return r


def full(pdga, name, to_par, **kw):
    """A completed round: to_par spread over the first holes."""
    strokes = [3] * 18
    for i in range(abs(to_par)):
        strokes[i] += -1 if to_par < 0 else 1
    return holes_row(pdga, name, strokes, **kw)


def serve(fake_api, sheets: dict[int, list[dict]], rounds: int = 3, tid: int = 1):
    fake_api.event(tid, event_payload("Rain Open", rounds, [("MPO", max(sheets))]))
    for rnd, rows in sheets.items():
        fake_api.round(tid, "MPO", rnd, round_payload(rows))


# ------------------------------------------------------ counted_standing

def test_lightning_counts_only_the_holes_everyone_completed(fake_api):
    """The early card is through 18, the late one through 9. Holes 10-18 are
    thrown away for everyone — so A's back-nine birdies vanish and B, who
    leads the live board by less, leads the counted one."""
    a = [3] * 9 + [2] * 9                  # even front, -9 back: -9 live
    b = [2, 2, 3, 3, 3, 3, 3, 3, 3] + [None] * 9   # -2 through 9
    c = [3] * 12 + [None] * 6              # even through 12
    serve(fake_api, {
        1: [full(1, "A", -3), full(2, "B", -3), full(3, "C", -3)],
        2: [holes_row(1, "A", a), holes_row(2, "B", b), holes_row(3, "C", c)],
    })
    got = live_api.counted_standing(1, "MPO")
    assert got["basis"]["rounds_complete"] == 1
    assert got["basis"]["partial_round"] == 2
    assert got["basis"]["partial_holes"] == list(range(1, 10))
    assert got["players"][1] == {"cur": -3.0, "place": 2}    # front nine only
    assert got["players"][2] == {"cur": -5.0, "place": 1}
    assert got["players"][3] == {"cur": -3.0, "place": 2}    # tied with A


def test_a_player_yet_to_tee_off_voids_the_round_in_progress(fake_api):
    """Nobody's holes count until everyone has some: a late card still on the
    range means the round in progress counts for nothing at all."""
    serve(fake_api, {
        1: [full(1, "A", -1), full(2, "B", -2)],
        2: [holes_row(1, "A", [2] * 6 + [None] * 12), holes_row(2, "B", [None] * 18)],
    })
    got = live_api.counted_standing(1, "MPO")
    assert got["basis"]["partial_round"] is None and got["basis"]["rounds_complete"] == 1
    assert got["players"][1]["cur"] == -1.0 and got["players"][2]["place"] == 1


def test_shotgun_start_with_no_common_hole_counts_nothing(fake_api):
    """Cards starting on different holes: the intersection is by hole, not by
    how many each has played — five holes each, none in common."""
    serve(fake_api, {
        1: [full(1, "A", 0), full(2, "B", -1)],
        2: [holes_row(1, "A", [2] * 5 + [None] * 13),
            holes_row(2, "B", [None] * 9 + [4] * 5 + [None] * 4)],
    })
    got = live_api.counted_standing(1, "MPO")
    assert got["basis"]["partial_round"] is None
    assert got["players"][2] == {"cur": -1.0, "place": 1}


def test_shotgun_overlap_counts_the_shared_holes(fake_api):
    serve(fake_api, {
        1: [full(1, "A", 0), full(2, "B", 0)],
        2: [holes_row(1, "A", [2] * 8 + [None] * 10),          # holes 1-8
            holes_row(2, "B", [None] * 5 + [4] * 6 + [None] * 7)],  # holes 6-11
    })
    got = live_api.counted_standing(1, "MPO")
    assert got["basis"]["partial_holes"] == [6, 7, 8]
    assert got["players"][1]["cur"] == -3.0 and got["players"][2]["cur"] == 3.0


def test_finals_non_qualifiers_do_not_block_the_count(fake_api):
    """A finals sheet lists the whole field; the non-qualifiers have no tee
    time and never play it. They keep their total and finish behind every
    finalist, even one who scored worse."""
    serve(fake_api, {
        1: [full(1, "A", -5), full(2, "B", -4), full(3, "C", -3)],
        12: [full(1, "A", 4), full(2, "B", 0),
             holes_row(3, "C", [None] * 18, tee_time="")],
    }, rounds=12)
    got = live_api.counted_standing(1, "MPO")
    assert got["basis"]["rounds_complete"] == 2
    assert [got["players"][p]["place"] for p in (1, 2, 3)] == [2, 1, 3]
    assert got["players"][3]["cur"] == -3.0   # better than A, still behind


def test_a_partial_round_without_hole_data_is_dropped_and_reported(fake_api):
    serve(fake_api, {
        1: [full(1, "A", -2), full(2, "B", -1)],
        2: [row(1, "A", round_to_par=-4, played=9), row(2, "B", round_to_par=0, played=7)],
    })
    got = live_api.counted_standing(1, "MPO")
    assert got["basis"]["unreadable"] == [2]
    assert got["players"][1]["cur"] == -2.0


def test_withdrawn_players_are_not_in_the_count(fake_api):
    serve(fake_api, {
        1: [full(1, "A", -2), full(2, "B", -1), full(3, "C", -9)],
        2: [holes_row(1, "A", [3] * 18), holes_row(2, "B", [3] * 18),
            {**row(3, "C", played=0, grand_total="999")}],
    })
    got = live_api.counted_standing(1, "MPO")
    assert set(got["players"]) == {1, 2}


def test_nothing_counted_yet_is_none(fake_api):
    serve(fake_api, {1: [holes_row(1, "A", [None] * 18), holes_row(2, "B", [2] + [None] * 17)]})
    assert live_api.counted_standing(1, "MPO") is None


# --------------------------------------------------------- asis.compute

def _sched_row(tid, cls, start, end, completed):
    return {"tournament_id": tid, "name": f"E{tid}", "cls": cls,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "completed": completed, "mpo": True, "fpo": True, "fpo_points": True}


@pytest.fixture
def mvp_weekend(monkeypatch):
    """Season down to the MVP Open (live) and the Cup, cut 2 of a field of 3.

    Banked: P1 200 (with a win), P2 150, P3 140, P4 130. Counted MVP places:
    P4 1st, P3 2nd, P5 (no standings row) 3rd, P1 4th, P2 5th.
    """
    today = dt.date.today()
    sched = [
        _sched_row(700, "elite", today - dt.timedelta(days=30), today - dt.timedelta(days=28), True),
        _sched_row(config.TID_MVP, "playoff", today - dt.timedelta(days=1), today + dt.timedelta(days=1), False),
        _sched_row(config.TID_CHAMPIONSHIP, "championship", today + dt.timedelta(days=20),
                   today + dt.timedelta(days=22), False),
    ]
    monkeypatch.setattr(points, "_cls_by_tid", lambda: {r["tournament_id"]: r["cls"] for r in sched})
    table = [
        {"pdga_number": 1, "rank": 1, "points": 200.0, "events": [(700, 200.0, 1, "E700")]},
        {"pdga_number": 2, "rank": 2, "points": 150.0, "events": [(700, 150.0, 2, "E700")]},
        {"pdga_number": 3, "rank": 3, "points": 140.0, "events": [(700, 140.0, 3, "E700")]},
        {"pdga_number": 4, "rank": 4, "points": 130.0, "events": [(700, 130.0, 4, "E700")]},
    ]
    standing = {
        "basis": {"total_rounds": 4, "rounds_complete": 2, "partial_round": 3,
                  "partial_holes": [1, 2, 3], "unreadable": []},
        "players": {4: {"cur": -12.0, "place": 1}, 3: {"cur": -10.0, "place": 2},
                    5: {"cur": -9.0, "place": 3}, 1: {"cur": -8.0, "place": 4},
                    2: {"cur": -7.0, "place": 5}},
    }
    monkeypatch.setattr(live_api, "counted_standing", lambda tid, div: standing)
    return sched, table


def test_as_is_banks_the_counted_places_and_reranks(mvp_weekend):
    sched, table = mvp_weekend
    got = asis.compute("MPO", table, sched, cut=2, field_size=3)
    curve = points.event_curve("MPO", "playoff")
    p4 = got["players"][4]
    assert p4["event"][config.TID_MVP]["pts"] == pytest.approx(curve[1])
    assert p4["points"] == pytest.approx(130.0 + curve[1])
    assert got["players"][4]["rank"] == 1     # 130 + a playoff win passes 200 + 4th
    ranks = sorted(v["rank"] for v in got["players"].values())
    assert ranks == list(range(1, 6))
    assert got["meta"]["decides_cup"] is True
    assert got["meta"]["events"][0]["partial_holes"] == [1, 2, 3]


def test_as_is_settles_the_cup_field(mvp_weekend):
    """Top-2 auto bids, one MVP-performance bid for the best finisher outside
    them, and the banked winner's special invite."""
    sched, table = mvp_weekend
    got = asis.compute("MPO", table, sched, cut=2, field_size=3)
    cup = {p: v["cup"] for p, v in got["players"].items()}
    auto = {p for p, c in cup.items() if c == "auto"}
    assert auto == {p for p, v in got["players"].items() if v["rank"] <= 2}
    assert list(cup.values()).count("mvp") == 1
    mvp_bid = next(p for p, c in cup.items() if c == "mvp")
    outside = [p for p in (4, 3, 5, 1, 2) if p not in auto]   # MVP finishing order
    assert mvp_bid == outside[0]
    assert cup[1] in ("auto", "invite")        # banked a win: in either way
    ladder = config.cup_start_strokes("MPO")
    for p, v in got["players"].items():
        if v["cup"]:
            assert v["strokes"] == ladder[min(v["rank"], len(ladder)) - 1]
        else:
            assert v["strokes"] is None


def test_as_is_does_not_settle_the_cup_with_events_still_to_play(mvp_weekend):
    sched, table = mvp_weekend
    today = dt.date.today()
    sched.insert(2, _sched_row(701, "elite", today + dt.timedelta(days=7),
                               today + dt.timedelta(days=9), False))
    got = asis.compute("MPO", table, sched, cut=2, field_size=3)
    assert got["meta"]["decides_cup"] is False
    assert all("cup" not in v for v in got["players"].values())


def test_a_failure_is_a_missing_panel_not_a_failed_run(mvp_weekend, monkeypatch, capsys):
    sched, table = mvp_weekend

    def boom(tid, div):
        raise KeyError("shape change")

    monkeypatch.setattr(live_api, "counted_standing", boom)
    assert asis.run("MPO", table, sched, cut=2, field_size=3) is None
    assert "as-is standings" in capsys.readouterr().err
