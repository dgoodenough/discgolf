"""The doubles championship: while it is being played, and once it banks.

Every other live event is drawn by _draw_singles, which reads the live state.
The doubles championship has its own team-level draw, and these tests pin that
it honours the same live state: a team's posted score locks in, both members
of a team carry the team's position, and the day tracker gets a projection.

Banking is the same story one step later — PDGA publishes one row per team, so
the partner has to be recovered from the pairing or they bank nothing.
"""
from __future__ import annotations

import csv
import datetime as dt

import pytest

from dgpt import config, points, schedule, simulate, standings
from tests.conftest import event_payload, round_payload, row

N_SIMS = 400

# entrants of record (PDGA Live lists one row per team) and their partners
LEADER, LEADER_MATE = 5004, 5005
CHASER, CHASER_MATE = 5006, 5007
SOLO = 5008


def _reschedule_doubles(start: str, end: str, completed: bool) -> None:
    rows = schedule.load()
    for r in rows:
        if r["tournament_id"] == config.TID_DOUBLES:
            r["start_date"], r["end_date"], r["completed"] = start, end, completed
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in schedule.FIELDS})
    points.refresh_classes()


# the sheet's shape: one row per team, carrying the partner under Teammates
MATES = {
    LEADER: [{"PDGANum": LEADER_MATE, "Name": "Player 05"}],
    CHASER: [{"PDGANum": CHASER_MATE, "Name": "Player 07"}],
    SOLO: [],
}
NAMES = {LEADER: "Player 04", CHASER: "Player 06", SOLO: "Player 08"}
RATINGS = {LEADER: 1034, CHASER: 1030, SOLO: 1026}
ENTRANTS = (LEADER, CHASER, SOLO)


def _make_doubles_live(world, fake_api) -> None:
    """Move the tiny world's doubles championship into the present and give it
    a round 1 sheet complete plus a round 2 half played."""
    today = dt.date.today()
    end = (today + dt.timedelta(days=1)).isoformat()
    _reschedule_doubles((today - dt.timedelta(days=1)).isoformat(), end, False)

    r1 = {LEADER: -14.0, CHASER: -9.0, SOLO: -7.0}
    r2 = {LEADER: -8.0, CHASER: -3.0, SOLO: -2.0}
    place = {LEADER: 1, CHASER: 2, SOLO: 3}

    fake_api.event(config.TID_DOUBLES,
                   event_payload("Test Doubles", 3, [("MPO", 2)], end_date=end))
    fake_api.round(config.TID_DOUBLES, "MPO", 1, round_payload([
        row(p, NAMES[p], RATINGS[p], round_to_par=r1[p], to_par=r1[p], played=18,
            has_score=True, teammates=MATES[p])
        for p in ENTRANTS
    ]))
    fake_api.round(config.TID_DOUBLES, "MPO", 2, round_payload([
        row(p, NAMES[p], RATINGS[p], round_to_par=r2[p], played=9,
            running_place=place[p], teammates=MATES[p])
        for p in ENTRANTS
    ]))


def _make_doubles_banked(fake_api, places: dict[int, int], mates=None) -> None:
    """A finished doubles championship: three rounds posted, final placings as
    given (one row per team, as PDGA publishes it)."""
    today = dt.date.today()
    end = (today - dt.timedelta(days=2)).isoformat()
    _reschedule_doubles((today - dt.timedelta(days=4)).isoformat(), end, True)
    mates = MATES if mates is None else mates

    fake_api.event(config.TID_DOUBLES,
                   event_payload("Test Doubles", 3, [("MPO", 3)], end_date=end))
    for rnd in (1, 2, 3):
        fake_api.round(config.TID_DOUBLES, "MPO", rnd, round_payload([
            row(p, NAMES[p], RATINGS[p], round_to_par=-10.0, played=18, has_score=True,
                running_place=places[p], grand_total=180, teammates=mates[p])
            for p in ENTRANTS
        ]))


@pytest.fixture
def doubles_live(tiny_world, fake_api):
    _make_doubles_live(tiny_world, fake_api)
    return simulate.run("MPO", n_sims=N_SIMS, chunk=200)


def test_live_doubles_event_is_tracked(doubles_live):
    """The day tracker needs a live projection for the event in progress."""
    per = doubles_live.live_stats.get(config.TID_DOUBLES)
    assert per, "no live projection for the doubles championship"


def test_live_doubles_locks_in_the_posted_team_score(doubles_live):
    per = doubles_live.live_stats[config.TID_DOUBLES]
    # entrant of record and partner both show the TEAM's score and standing
    for member in (LEADER, LEADER_MATE):
        assert per[member]["cur"] == pytest.approx(-22.0)
        assert per[member]["thru"] == 27
        assert per[member]["place"] == 1
    for member in (CHASER, CHASER_MATE):
        assert per[member]["cur"] == pytest.approx(-12.0)
        assert per[member]["place"] == 2
    assert per[SOLO]["cur"] == pytest.approx(-9.0)


def test_live_doubles_leader_is_the_favourite(doubles_live):
    per = doubles_live.live_stats[config.TID_DOUBLES]
    # a 10-shot lead with a round and a half left is close to decisive, and
    # both members of the team hold the same odds
    assert per[LEADER]["win"] > per[CHASER]["win"]
    assert per[LEADER]["win"] == per[LEADER_MATE]["win"]
    assert per[LEADER]["win"] > 0.8


def test_a_playoff_result_is_not_a_coin_flip(tiny_world, fake_api):
    """An event decided in sudden death.

    A playoff is scored on its own sheet and its strokes are never folded into
    anyone's total, so the teams that went to it stay level on score with no
    holes left. Ranked on score alone that reads as a coin flip after the
    event is over — the 2026 doubles championship published the team that WON
    the playoff at 49.78% and the team it beat at 50.22%. The sheet's
    RunningPlace is the resolved answer and has to decide it.
    """
    today = dt.date.today()
    end = today.isoformat()
    _reschedule_doubles((today - dt.timedelta(days=2)).isoformat(), end, False)

    # three rounds played; the top two teams tie on score, the sheet resolves
    # them 1 and 2 (as it does once the playoff is over)
    total = {LEADER: -26.0, CHASER: -26.0, SOLO: -20.0}
    place = {LEADER: 1, CHASER: 2, SOLO: 3}
    fake_api.event(config.TID_DOUBLES,
                   event_payload("Test Doubles", 3, [("MPO", 3)], end_date=end))
    for rnd in (1, 2, 3):
        fake_api.round(config.TID_DOUBLES, "MPO", rnd, round_payload([
            row(p, NAMES[p], RATINGS[p], round_to_par=total[p] / 3, played=18,
                has_score=True, running_place=place[p], teammates=MATES[p])
            for p in ENTRANTS
        ]))

    per = simulate.run("MPO", n_sims=N_SIMS, chunk=200).live_stats[config.TID_DOUBLES]
    assert per[LEADER]["rem"] == 0.0 and per[LEADER]["thru"] == 54
    assert per[LEADER]["cur"] == per[CHASER]["cur"]     # level on score
    assert per[LEADER]["win"] == 1.0                    # ... but the playoff is decided
    assert per[LEADER_MATE]["win"] == 1.0
    assert per[CHASER]["win"] == 0.0


# ------------------------------------------------------------------ banking

def _doubles_banked(table: list[dict]) -> dict[int, tuple[float, int]]:
    """{pdga: (points, place)} for the doubles championship, per the standings."""
    out = {}
    for r in table:
        for tid, pts, place, _ in r["events"]:
            if tid == config.TID_DOUBLES:
                out[r["pdga_number"]] = (pts, place)
    return out


def test_both_team_members_bank_the_doubles_result(tiny_world, fake_api):
    """The partner has no row on the results sheet, and used to bank nothing
    from an event they played — and, at the top of the sheet, won."""
    _make_doubles_banked(fake_api, {LEADER: 1, CHASER: 2, SOLO: 3})
    banked = _doubles_banked(standings.compute("MPO"))

    curve = points.event_curve("MPO", "doubles")
    for member in (LEADER, LEADER_MATE):
        assert banked[member] == (pytest.approx(curve[1]), 1)
    for member in (CHASER, CHASER_MATE):
        assert banked[member] == (pytest.approx(curve[2]), 2)
    # a team registered without a listed partner still banks its own result
    assert banked[SOLO] == (pytest.approx(curve[3]), 3)


def test_doubles_ties_are_averaged_over_teams_not_players(tiny_world, fake_api):
    """Tie averaging has to see each team once. Paying members off an expanded
    row list would read every team as a two-way tie and average in the place
    below it — a solo winner banking 123.75 instead of 137.5."""
    _make_doubles_banked(fake_api, {LEADER: 1, CHASER: 1, SOLO: 3})
    banked = _doubles_banked(standings.compute("MPO"))

    curve = points.event_curve("MPO", "doubles")
    tied = (curve[1] + curve[2]) / 2
    for member in (LEADER, LEADER_MATE, CHASER, CHASER_MATE):
        assert banked[member][0] == pytest.approx(tied)
    assert banked[SOLO][0] == pytest.approx(curve[3])


def test_doubles_partner_keeps_the_name_from_their_own_results(tiny_world, fake_api):
    """A pairing entry can carry a PDGA number and no name; banking it must not
    blank out the name the player's own results supplied."""
    nameless = {**MATES, LEADER: [{"PDGANum": LEADER_MATE}]}
    _make_doubles_banked(fake_api, {LEADER: 1, CHASER: 2, SOLO: 3}, mates=nameless)
    by_pdga = {r["pdga_number"]: r for r in standings.compute("MPO")}
    assert _doubles_banked([by_pdga[LEADER_MATE]])[LEADER_MATE][1] == 1
    assert by_pdga[LEADER_MATE]["name"] == "Player 05"
