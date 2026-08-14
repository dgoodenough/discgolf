"""The doubles championship while it is being played.

Every other live event is drawn by _draw_singles, which reads the live state.
The doubles championship has its own team-level draw, and these tests pin that
it honours the same live state: a team's posted score locks in, both members
of a team carry the team's position, and the day tracker gets a projection.
"""
from __future__ import annotations

import csv
import datetime as dt

import pytest

from dgpt import config, points, schedule, simulate
from tests.conftest import event_payload, round_payload, row

N_SIMS = 400

# entrants of record (PDGA Live lists one row per team) and their partners
LEADER, LEADER_MATE = 5004, 5005
CHASER, CHASER_MATE = 5006, 5007
SOLO = 5008


def _make_doubles_live(world, fake_api) -> None:
    """Move the tiny world's doubles championship into the present and give it
    a round 1 sheet complete plus a round 2 half played."""
    today = dt.date.today()
    rows = schedule.load()
    for r in rows:
        if r["tournament_id"] == config.TID_DOUBLES:
            r["start_date"] = (today - dt.timedelta(days=1)).isoformat()
            r["end_date"] = (today + dt.timedelta(days=1)).isoformat()
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in schedule.FIELDS})
    points.refresh_classes()

    mates = {
        LEADER: [{"PDGANum": LEADER_MATE, "Name": "Player 05"}],
        CHASER: [{"PDGANum": CHASER_MATE, "Name": "Player 07"}],
        SOLO: [],
    }
    names = {LEADER: "Player 04", CHASER: "Player 06", SOLO: "Player 08"}
    ratings = {LEADER: 1034, CHASER: 1030, SOLO: 1026}
    r1 = {LEADER: -14.0, CHASER: -9.0, SOLO: -7.0}
    r2 = {LEADER: -8.0, CHASER: -3.0, SOLO: -2.0}
    place = {LEADER: 1, CHASER: 2, SOLO: 3}

    fake_api.event(
        config.TID_DOUBLES,
        event_payload("Test Doubles", 3, [("MPO", 2)],
                      end_date=(today + dt.timedelta(days=1)).isoformat()),
    )
    fake_api.round(config.TID_DOUBLES, "MPO", 1, round_payload([
        row(p, names[p], ratings[p], round_to_par=r1[p], to_par=r1[p], played=18,
            has_score=True, teammates=mates[p])
        for p in (LEADER, CHASER, SOLO)
    ]))
    fake_api.round(config.TID_DOUBLES, "MPO", 2, round_payload([
        row(p, names[p], ratings[p], round_to_par=r2[p], played=9,
            running_place=place[p], teammates=mates[p])
        for p in (LEADER, CHASER, SOLO)
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
