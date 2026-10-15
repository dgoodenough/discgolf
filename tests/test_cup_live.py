"""The Powerball Cup in progress: its starting strokes and its live odds.

PDGA Live carries the Cup's seed head start as a fake round labelled "* adv"
— no holes, just each player's advantage as their score. Two things have to
hold: that pseudo-round is never mistaken for a round (it would hand everyone
a phantom 18 holes, and on a four-day event push the real final round out of
the plan), and its strokes are part of the score the odds are drawn on.
Whether they are in the scores is detected, not assumed (sideodds.start_strokes).
"""
from __future__ import annotations

import csv
import datetime as dt

import pytest

from dgpt import config, live_api, schedule, sideodds, simulate
from tests.conftest import event_payload, round_payload, row

CUP = config.TID_CHAMPIONSHIP
TODAY = dt.date.today()


def day(offset: int) -> str:
    return (TODAY + dt.timedelta(days=offset)).isoformat()


def cup_event(adv_id: int = 1, rounds: tuple[int, ...] = (2, 3, 4, 5), latest: int = 2,
              division: str = "MPO") -> dict:
    ids = sorted((adv_id, *rounds))
    ev = event_payload("DGPT Powerball Cup", max(rounds), [(division, latest)], end_date=day(3),
                       round_ids=ids,
                       round_labels={adv_id: "* adv", **{r: f"Round {i}" for i, r in enumerate(rounds, 1)}})
    return {**ev, "StartDate": day(0)}


# Three players, R1 half played. The No. 1 seed starts -7, No. 2 -6, a
# bottom seed even; on the course the bottom seed is three better.
FIELD = [(7001, "Top Seed", 1040), (7002, "Second Seed", 1040), (7003, "Bottom Seed", 1040)]
SEED = {7001: -7, 7002: -6, 7003: 0}
R1 = {7001: 0, 7002: -1, 7003: -3}


def stage(fake_api, *, adv: bool = True, adv_id: int = 1, rounds=(2, 3, 4, 5),
          strokes_in_round=False, places=True) -> None:
    fake_api.event(CUP, cup_event(adv_id, rounds, latest=rounds[0]))
    if adv:
        fake_api.round(CUP, "MPO", adv_id, round_payload([
            row(p, n, r, round_to_par=SEED[p], has_score=True) for p, n, r in FIELD]))
    total = {p: SEED[p] + R1[p] for p in SEED}
    place = {p: 1 + sum(total[q] < total[p] for q in total) for p in total}
    fake_api.round(CUP, "MPO", rounds[0], round_payload([
        row(p, n, r, round_to_par=R1[p] + (SEED[p] if strokes_in_round else 0), played=9,
            running_place=place[p] if places else None)
        for p, n, r in FIELD]))


# ------------------------------------------------------------- live_api

def test_the_adv_round_is_not_a_round(fake_api):
    stage(fake_api)
    ev = live_api.fetch_event(CUP)
    assert live_api._real_rounds(ev) == [2, 3, 4, 5]   # the real final round survives the day cap
    assert live_api._adv_rounds(ev) == [1]


def test_the_adv_round_listed_last_is_not_a_round_either(fake_api):
    stage(fake_api, adv_id=5, rounds=(1, 2, 3, 4))
    assert live_api._real_rounds(live_api.fetch_event(CUP)) == [1, 2, 3, 4]


def test_live_field_carries_the_strokes_beside_the_score(fake_api):
    stage(fake_api)
    field = live_api.live_field(CUP, "MPO")
    assert field[7001]["cur"] == 0.0 and field[7001]["adv"] == -7.0
    assert field[7003]["cur"] == -3.0 and field[7003]["adv"] == 0.0
    # 9 of 72 holes played: the pseudo-round adds no holes
    assert field[7001]["rem"] == pytest.approx(63 / 18)
    assert field[7001]["thru"] == 9


def test_other_events_carry_no_strokes(fake_api):
    fake_api.event(900010, {**event_payload("Open", 3, [("MPO", 1)], end_date=day(2)),
                            "StartDate": day(0)})
    fake_api.round(900010, "MPO", 1, round_payload([row(1, "A", 1000, round_to_par=-2, played=9)]))
    assert live_api.live_field(900010, "MPO")[1]["adv"] == 0.0


# --------------------------------------------------------- detection

def state(cur: dict, place: dict | None = None, thru: int = 9, adv: dict | None = None) -> dict:
    return {p: {"cur": float(c), "place": (place or {}).get(p), "thru": thru,
                "adv": (adv or {}).get(p, 0.0)} for p, c in cur.items()}


def test_strokes_from_the_adv_round_are_used_as_given():
    head, how = sideodds.start_strokes(state(R1, adv=SEED), {p: 0.0 for p in SEED})
    assert head == {p: float(v) for p, v in SEED.items()} and "starting-strokes round" in how


def test_places_say_the_strokes_are_missing():
    total = {p: SEED[p] + R1[p] for p in SEED}
    place = {p: 1 + sum(total[q] < total[p] for q in total) for p in total}
    head, how = sideodds.start_strokes(state(R1, place), SEED)
    assert head == SEED and "seed table" in how


def test_places_say_the_strokes_are_already_in():
    total = {p: SEED[p] + R1[p] for p in SEED}
    place = {p: 1 + sum(total[q] < total[p] for q in total) for p in total}
    head, how = sideodds.start_strokes(state(total, place), SEED)
    assert head == {} and "already in" in how


def test_before_tee_off_a_seeded_score_means_already_in():
    head, _ = sideodds.start_strokes(state(SEED, thru=0), SEED)
    assert head == {}
    head, _ = sideodds.start_strokes(state({p: 0 for p in SEED}, thru=0), SEED)
    assert head == SEED


# ------------------------------------------------------------- the odds

RANKS = {7001: 1, 7002: 2, 7003: 30}   # 30th: past the ladder, starts even


@pytest.mark.parametrize("how", ["adv", "in_round", "missing"])
def test_the_head_start_counts_however_pdga_shows_it(fake_api, monkeypatch, how):
    monkeypatch.setattr("dgpt.ratings.current", lambda division: {p: r for p, _, r in FIELD})
    stage(fake_api, adv=how == "adv", strokes_in_round=how == "in_round")
    cup = sideodds.cup_stats("MPO", RANKS, n_sims=4000)
    s = cup.stats
    # same ratings, 63 holes left: the -7 vs -3 total decides it
    assert s[7001]["cur"] == -7.0 and s[7003]["cur"] == -3.0
    assert s[7001]["win"] > s[7002]["win"] > s[7003]["win"]
    assert abs(sum(v["win"] for v in s.values()) - 1) < 0.01


def test_no_live_field_no_odds(fake_api):
    fake_api.event(CUP, {**cup_event(), "Divisions": [{"Division": "MPO", "LatestRound": None}]})
    assert sideodds.cup_stats("MPO", RANKS) is None


# --------------------------------------------------- through simulate.run

def test_a_live_cup_drives_the_cup_odds_and_the_event_odds(tiny_world, fake_api, monkeypatch):
    """The Cup used to be played from scratch whatever the scoreboard said."""
    # finish the tiny season's schedule so the Cup is the one event live
    with open(schedule.SCHEDULE_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if int(r["tournament_id"]) == CUP:
            r["start_date"], r["end_date"] = day(0), day(3)
        elif r["completed"] == "False":
            r["start_date"] = r["end_date"] = day(-30)
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        w.writerows(rows)
    star, second = tiny_world.players[0][0], tiny_world.players[1][0]
    field = [(star, "Player 01", 1040), (second, "Player 02", 1038), (5010, "Player 10", 1022)]
    fake_api.event(CUP, cup_event())
    # Player 10 leads the round by five, but the No. 1 seed started seven up
    fake_api.round(CUP, "MPO", 1, round_payload([
        row(p, n, r, round_to_par=s, has_score=True)
        for (p, n, r), s in zip(field, (-7, -6, 0))]))
    fake_api.round(CUP, "MPO", 2, round_payload([
        row(p, n, r, round_to_par=s, played=9, running_place=pl)
        for (p, n, r), s, pl in zip(field, (0, 0, -5), (1, 2, 3))]))

    res = simulate.run("MPO", n_sims=2000, chunk=500)
    live = res.live_stats[CUP]
    assert set(live) == {p for p, _, _ in field}
    assert live[star]["cur"] == -7.0
    by = dict(zip(res.pdga_numbers, res.p_cup_win))
    assert by[star] == pytest.approx(live[star]["win"])
    assert by[star] > by[5010] > 0
    # nobody outside the live field can win it any more
    assert sum(by.values()) == pytest.approx(1.0, abs=0.01)


def test_a_finished_cup_keeps_its_result(tiny_world, fake_api):
    with open(schedule.SCHEDULE_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if int(r["tournament_id"]) == CUP:
            r["start_date"], r["end_date"], r["completed"] = day(-5), day(-2), "True"
        elif r["completed"] == "False":
            r["start_date"] = r["end_date"] = day(-30)
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        w.writerows(rows)
    star, second = tiny_world.players[0][0], tiny_world.players[1][0]
    field = [(star, "Player 01", 1040), (second, "Player 02", 1038)]
    fake_api.event(CUP, {**cup_event(latest=5), "StartDate": day(-5), "EndDate": day(-2)})
    fake_api.round(CUP, "MPO", 1, round_payload([
        row(p, n, r, round_to_par=s, has_score=True) for (p, n, r), s in zip(field, (-7, -6))]))
    for rnd in (2, 3, 4, 5):   # the second seed wins it by two
        fake_api.round(CUP, "MPO", rnd, round_payload([
            row(p, n, r, round_to_par=s, played=18, has_score=True, running_place=pl)
            for (p, n, r), s, pl in zip(field, (0, -1 - (rnd == 5) * 2), (2, 1))]))
    res = simulate.run("MPO", n_sims=1000, chunk=500)
    by = dict(zip(res.pdga_numbers, res.p_cup_win))
    assert by[second] == pytest.approx(1.0) and by[star] == 0.0
