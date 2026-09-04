"""Signup-driven playoff fields and the Pro Worlds play-in.

Both change the same thing: who is in a field the standings used to decide
alone. The playoffs now read their published signup lists and take them
literally, and Worlds gains six MPO spots that are won on the course rather
than on points.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

import numpy as np
import pytest

from dgpt import config, export, fields, live_api, points, schedule, simulate, standings
from tests.conftest import event_payload, round_payload, row

N_SIMS = 400
# Enough listed players to clear live_api.MIN_PAGE_REGISTRANTS.
LISTED = 10


@pytest.fixture
def tight_qual(monkeypatch):
    """Playoff windows scaled to a 31-player world.

    The real ones (top 100/50 into GMC) admit every player the tiny world
    has, so nothing a signup does would be visible against them.
    """
    monkeypatch.setattr(config, "PLAYOFF_QUAL", {
        "gmc": {"cut": {"MPO": 8, "FPO": 8}, "fill": {"MPO": 10, "FPO": 10}},
        "mvp": {"cut": {"MPO": 6, "FPO": 6}, "perf": {"MPO": 2, "FPO": 2}},
    })


@pytest.fixture
def waves_open(monkeypatch):
    """Every registration window has opened, so a staged roster can be final."""
    monkeypatch.setattr(fields, "_waves_all_open", lambda tid, now=None: True)


# ------------------------------------------------------------ signup lists

def test_page_registrants_reads_the_public_signup_list(fake_pages):
    fake_pages.signups(config.TID_GMC, range(5001, 5021))
    assert live_api.page_registrants(config.TID_GMC) == frozenset(range(5001, 5021))


def test_page_without_a_registration_marker_is_not_a_field(fake_pages):
    """A 404 shell or a markup rewrite must read as "no list", never as one."""
    links = "".join(f'<a href="/player/{p}">x</a>' for p in range(5001, 5021))
    fake_pages.raw(config.TID_GMC, f"<h1>Page not found</h1>{links}")
    assert live_api.page_registrants(config.TID_GMC) == frozenset()


def test_a_page_linking_only_its_td_is_not_a_field(fake_pages):
    fake_pages.raw(config.TID_GMC, '<h4>Registered Players</h4><a href="/player/42">TD</a>')
    assert live_api.page_registrants(config.TID_GMC) == frozenset()


def test_the_page_is_fetched_once_per_process(fake_pages, monkeypatch):
    fake_pages.signups(config.TID_MVP, range(6001, 6021))
    calls = []
    inner = live_api._fetch_page
    monkeypatch.setattr(live_api, "_fetch_page", lambda url: (calls.append(url), inner(url))[1])
    live_api.page_registrants(config.TID_MVP)
    live_api.page_registrants(config.TID_MVP)
    assert len(calls) == 1


def test_pdga_live_outranks_the_page(fake_api, fake_pages):
    """Once a TD stages the event, its roster is the authority."""
    fake_api.round(config.TID_GMC, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in (7001, 7002)]))
    fake_pages.signups(config.TID_GMC, range(5001, 5021))
    entries, final = live_api.registration_list(config.TID_GMC, "MPO")
    assert set(entries) == {7001, 7002}
    assert final is True   # a staged event is the field, not a floor


def test_no_list_anywhere_is_none_not_empty(fake_api, fake_pages):
    """None keeps the pre-signup standings gate; {} would empty the field."""
    assert live_api.registration_list(config.TID_GMC, "MPO") is None
    assert fields.signed_up(config.TID_GMC, "MPO", [5001, 5002]) is None


def test_overrides_win_over_the_published_list(fake_api, fake_pages, tmp_path, monkeypatch):
    fake_pages.signups(config.TID_GMC, range(5001, 5021))
    ov = tmp_path / "fields.csv"
    with open(ov, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tournament_id", "pdga_number", "plays"])
        w.writerow([config.TID_GMC, 5001, "0"])   # listed, forced out
        w.writerow([config.TID_GMC, 9999, "1"])   # unlisted, forced in
    monkeypatch.setattr(fields, "OVERRIDES_CSV", ov)

    signed = fields.signed_up(config.TID_GMC, "MPO", [5001, 5002, 9999])
    assert signed.players == {5002, 9999}
    assert signed.final is False   # a hand-written override means the list is suspect


# ------------------------------------------------- signups inside the sim

def test_a_signed_up_player_is_in_the_gmc_field_whatever_their_rank(
        tiny_world, fake_pages, tight_qual):
    """The point of taking signups literally: a tour-card entrant ranked well
    outside the qualifying window is in the field anyway, and the sim has to
    stop pretending the standings decide it."""
    standings.write_csv("MPO", standings.compute("MPO"))
    tail = [p for p, _, _ in tiny_world.players[-LISTED:]]
    fake_pages.signups(config.TID_GMC, tail)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    for pdga in tail:
        i = res.pdga_numbers.index(pdga)
        assert res.reg_gmc[i]
        assert res.p_gmc_field[i] == pytest.approx(1.0)
        # ...and it is the signup doing the work, not the standings
        assert res.p_gmc[i] < 0.5


def test_an_unsigned_contender_still_qualifies_through_the_gate(
        tiny_world, fake_pages, tight_qual):
    """The waves reaching top 100 MPO / 50 FPO have not all opened, so an
    absence from today's list is not an absence from the field."""
    standings.write_csv("MPO", standings.compute("MPO"))
    fake_pages.signups(config.TID_GMC, [p for p, _, _ in tiny_world.players[-LISTED:]])

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    star = res.pdga_numbers.index(tiny_world.star)
    assert not res.reg_gmc[star]
    assert res.p_gmc_field[star] == pytest.approx(1.0)

    # and the gate is still a gate: a mid-table player who has not signed up
    # gets in only when the standings put them there
    mid = res.pdga_numbers.index(tiny_world.players[14][0])
    assert not res.reg_gmc[mid]
    assert res.p_gmc_field[mid] < 1.0


def test_signups_do_not_move_the_points_cut_odds(tiny_world, fake_pages, tight_qual):
    """p_gmc_cut is a standings question — registering does not answer it."""
    standings.write_csv("MPO", standings.compute("MPO"))
    before = simulate.run("MPO", n_sims=N_SIMS, chunk=200)

    live_api._page_memo.clear()
    fake_pages.signups(config.TID_GMC, [p for p, _, _ in tiny_world.players[-LISTED:]])
    after = simulate.run("MPO", n_sims=N_SIMS, chunk=200)

    assert np.allclose(before.p_gmc, after.p_gmc)
    assert not np.allclose(before.p_gmc_field, after.p_gmc_field)


def test_mvp_signup_does_not_burn_a_gmc_performance_spot(tiny_world, fake_pages, tight_qual):
    """The 8 MPO / 4 FPO performance spots exist for players with no other way
    in. Keying eligibility off the points cut instead of the field would spend
    them on players who already hold a signed-up berth, and the MVP field
    would come out smaller than the rules allow.
    """
    standings.write_csv("MPO", standings.compute("MPO"))
    everyone = [p for p, _, _ in tiny_world.players]
    signed = everyone[:LISTED]
    fake_pages.signups(config.TID_GMC, everyone)   # everybody plays GMC
    fake_pages.signups(config.TID_MVP, signed)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    perf = config.PLAYOFF_QUAL["mvp"]["perf"]["MPO"]
    is_signed = np.array([p in set(signed) for p in res.pdga_numbers])

    # field = signed up, plus the points cut, plus the performance spots, and
    # the two groups never overlap
    expected = is_signed.sum() + res.p_mvp[~is_signed].sum() + perf
    assert res.p_mvp_field.sum() == pytest.approx(expected, abs=1e-9)
    assert np.all(res.p_mvp_field[is_signed] == pytest.approx(1.0))


def test_a_roster_staged_before_the_last_wave_is_not_final(fake_api, fake_pages, monkeypatch):
    """The DGPT stages events weeks ahead — Idlewild was live on PDGA Live 17
    days out — and GMC's last wave opens Sep 1, six days before that lead time
    would reach it. A roster staged in the gap looks authoritative and is
    missing everyone wave 2 admits, so it must not close the field."""
    fake_api.round(config.TID_GMC, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in range(7001, 7021)]))

    class FrozenClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(dt, "datetime", FrozenClock)
    signed = fields.signed_up(config.TID_GMC, "MPO", list(range(7001, 7021)))
    assert len(signed.players) == 20
    assert signed.final is False   # wave 2 has not opened: still a floor


def test_the_same_roster_is_final_once_every_wave_has_opened(fake_api, monkeypatch):
    fake_api.round(config.TID_GMC, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in range(7001, 7021)]))

    class FrozenClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(dt, "datetime", FrozenClock)
    assert fields.signed_up(config.TID_GMC, "MPO", list(range(7001, 7021))).final is True


def test_the_play_in_has_no_waves_to_wait_for(fake_api):
    """Nothing gates entry to the play-in, so a staged roster is the field."""
    fake_api.round(config.TID_WORLDS_PLAYIN, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in range(7001, 7021)]))
    assert fields.signed_up(config.TID_WORLDS_PLAYIN, "MPO", list(range(7001, 7021))).final is True


def test_a_staged_roster_replaces_the_gate_instead_of_widening_it(
        tiny_world, fake_api, tight_qual, waves_open):
    """PDGA Live only carries an event once the field is set. Unioning that
    settled field with everyone the standings gate would admit invents
    entrants — and GMC points count, so it would invent points too."""
    standings.write_csv("MPO", standings.compute("MPO"))
    field = [p for p, _, _ in tiny_world.players[15:22]]
    fake_api.round(config.TID_GMC, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in field]))

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    inside = set(field)
    for pdga in [p for p, _, _ in tiny_world.players]:
        i = res.pdga_numbers.index(pdga)
        assert res.p_gmc_field[i] == pytest.approx(1.0 if pdga in inside else 0.0)
    # the star tops the standings and is still not in a field they did not enter
    assert res.p_gmc[res.pdga_numbers.index(tiny_world.star)] > 0.9


def test_a_final_mvp_field_needs_no_performance_spots(tiny_world, fake_api, tight_qual, waves_open):
    """The 8/4 GMC-performance places are already counted in a staged roster —
    handing out more would put players in the field twice over."""
    standings.write_csv("MPO", standings.compute("MPO"))
    field = [p for p, _, _ in tiny_world.players[15:22]]
    fake_api.round(config.TID_MVP, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in field]))

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    assert res.p_mvp_field.sum() == pytest.approx(len(field))


def test_a_staged_mvp_roster_still_admits_gmc_performers(tiny_world, fake_api, tight_qual):
    """Production's exact situation, and the bug it hid for three weeks.

    PDGA Live stages the MVP Open well before it is played, and by Sep 1 both
    invite waves had opened — so the roster read as final. _playoff_field then
    replaces the gate with the list, and the `not mvp_final` guard switches the
    GMC-performance path off with it: every player whose only road to the Cup
    ran through a big GMC went to exactly 0.000, points leaders included,
    because they simply had not filed their entry yet.

    The performance phase keeps the roster open until GMC has actually been
    played, so the field is the list PLUS the points gate PLUS the spots GMC
    decides. Compare against the same roster held final, which is the
    behaviour that was wrong here and stays right after the event.
    """
    standings.write_csv("MPO", standings.compute("MPO"))
    listed = [p for p, _, _ in tiny_world.players[15:22]]
    fake_api.round(config.TID_MVP, "MPO", 1, round_payload(
        [row(p, f"P{p}", 1000, played=0) for p in listed]))

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    assert not res.mvp_final, "GMC has not been played, so the field is not set"

    is_listed = np.array([p in set(listed) for p in res.pdga_numbers])
    assert np.all(res.p_mvp_field[is_listed] == pytest.approx(1.0))
    # the whole point: players off the list are not written off
    assert res.p_mvp_field[~is_listed].sum() > 0.0
    perf = config.PLAYOFF_QUAL["mvp"]["perf"]["MPO"]
    expected = is_listed.sum() + res.p_mvp[~is_listed].sum() + perf
    assert res.p_mvp_field.sum() == pytest.approx(expected, abs=1e-9)


# ------------------------------------------------------------- the play-in

def _worlds_world(tiny_world, fake_api, *, worlds_field, playin_field, pages=None,
                  ratings_by_pdga=None):
    """Re-cast the tiny world's future major as Pro Worlds, with a play-in."""
    rows = schedule.load()
    for r in rows:
        if r["tournament_id"] == 900004:
            r["tournament_id"] = config.TID_WORLDS
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        w.writerows(rows)
    points.refresh_classes()

    rate = ratings_by_pdga or {}
    fake_api.event(config.TID_WORLDS, event_payload("Worlds", 4, [("MPO", None)]))
    fake_api.round(config.TID_WORLDS, "MPO", 1, round_payload(
        [row(p, f"P{p}", rate.get(p, 1000), played=0) for p in worlds_field]))
    if pages is not None:
        pages.signups(config.TID_WORLDS_PLAYIN, playin_field)
    else:
        fake_api.round(config.TID_WORLDS_PLAYIN, "MPO", 1, round_payload(
            [row(p, f"P{p}", rate.get(p, 1000), played=0) for p in playin_field]))
    standings.write_csv("MPO", standings.compute("MPO"))


def test_play_in_promotes_entrants_into_the_worlds_field(tiny_world, fake_api):
    """Six spots, six rostered entrants and nobody else in the play-in: all
    six are at Worlds in every sim, having entered nothing else."""
    entrants = [p for p, _, _ in tiny_world.players[10:16]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    worlds_ei = next(i for i, e in enumerate(res.events_meta)
                     if e["tid"] == config.TID_WORLDS)
    assert len(entrants) == config.WORLDS_PLAYIN_SPOTS["MPO"]
    for pdga in entrants:
        i = res.pdga_numbers.index(pdga)
        assert res.p_playin[i] == pytest.approx(1.0)
        assert res.att_probs[worlds_ei, i] == pytest.approx(1.0)


def test_play_in_spots_are_contested_not_handed_out(tiny_world, fake_api):
    """Twice as many entrants as spots: everyone has a real chance and the
    spots stay scarce — the whole field cannot advance."""
    entrants = [p for p, _, _ in tiny_world.players[10:22]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    p = np.array([res.p_playin[res.pdga_numbers.index(x)] for x in entrants])
    assert np.all((p > 0.0) & (p < 1.0))
    # exactly the available spots are filled, on average, in every sim
    assert p.sum() == pytest.approx(config.WORLDS_PLAYIN_SPOTS["MPO"], abs=1e-9)


def test_unrostered_entrants_still_take_spots(tiny_world, fake_api):
    """The real entry list is a hundred-odd players with no standings row.
    They are not in the table, but they must still be raced, or two rostered
    entrants would split six spots between them."""
    entrants = [p for p, _, _ in tiny_world.players[10:12]]
    outsiders = {9000 + i: 1035 for i in range(40)}  # unrostered and strong
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants + list(outsiders),
                  ratings_by_pdga=outsiders)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    p = np.array([res.p_playin[res.pdga_numbers.index(x)] for x in entrants])
    assert np.all(p < 0.9)


def test_a_direct_qualifier_does_not_also_take_a_play_in_spot(tiny_world, fake_api):
    both = [p for p, _, _ in tiny_world.players[:6]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=both,
                  playin_field=both)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    for pdga in both:
        assert res.p_playin[res.pdga_numbers.index(pdga)] == pytest.approx(0.0)


def test_play_in_reads_the_event_page_when_live_scoring_is_dark(tiny_world, fake_api, fake_pages):
    entrants = [p for p, _, _ in tiny_world.players[10:22]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants, pages=fake_pages)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    p = np.array([res.p_playin[res.pdga_numbers.index(x)] for x in entrants])
    assert p.sum() == pytest.approx(config.WORLDS_PLAYIN_SPOTS["MPO"], abs=1e-9)


def test_no_play_in_list_leaves_the_worlds_field_alone(tiny_world, fake_api):
    field = [p for p, _, _ in tiny_world.players[:10]]
    _worlds_world(tiny_world, fake_api, worlds_field=field, playin_field=[])

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    worlds_ei = next(i for i, e in enumerate(res.events_meta)
                     if e["tid"] == config.TID_WORLDS)
    assert np.all(res.p_playin == 0.0)
    outside = [p for p, _, _ in tiny_world.players[10:]]
    for pdga in outside:
        i = res.pdga_numbers.index(pdga)
        assert res.att_probs[worlds_ei, i] == pytest.approx(0.0)


# ------------------------------------------------------ what the app reads

def test_bundle_carries_signups_and_play_in_odds(tiny_world, fake_api, fake_pages, tight_qual):
    entrants = [p for p, _, _ in tiny_world.players[10:22]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants)
    signed = [p for p, _, _ in tiny_world.players[:LISTED]]
    fake_pages.signups(config.TID_GMC, signed)

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    export.export(res)
    bundle = json.loads((export.DOCS_DATA / "mpo.json").read_text(encoding="utf-8"))

    meta = bundle["meta"]
    assert meta["gmc_signups"] == len(signed)
    assert meta["mvp_signups"] == 0            # no MVP list published
    assert meta["playin_tid"] == config.TID_WORLDS_PLAYIN
    assert meta["playin_spots"] == config.WORLDS_PLAYIN_SPOTS["MPO"]
    assert meta["playin_entrants"] == len(entrants)
    # the phases the page explains the remaining uncertainty with
    assert [ph["label"] for ph in meta["reg_phases"]["mvp"]] == [
        ph["label"] for ph in config.REG_PHASES["mvp"]]
    # per-division dicts flattened to the division's own number
    assert meta["reg_phases"]["gmc"][-1]["top"] == config.REG_PHASES["gmc"][-1]["top"]["MPO"]
    assert meta["reg_phases"]["gmc"][0]["min_rating"] == config.REG_PHASES["gmc"][0]["min_rating"]["MPO"]

    by_pdga = {p["pdga"]: p for p in bundle["players"]}
    assert all(by_pdga[p]["reg_gmc"] == 1 for p in signed)
    assert all(by_pdga[p]["reg_mvp"] == 0 for p in signed)
    assert all(0.0 < by_pdga[p]["p_playin"] < 1.0 for p in entrants)
    assert by_pdga[tiny_world.star]["p_playin"] == 0.0   # qualified directly


def _play_in_played(fake_api, entrants, places, *, latest=1):
    """Stage the play-in as an event that has been contested and finished."""
    fake_api.event(config.TID_WORLDS_PLAYIN,
                   event_payload("Worlds Play-In", 1, [("MPO", latest)]))
    fake_api.round(config.TID_WORLDS_PLAYIN, "MPO", 1, round_payload([
        row(p, f"P{p}", 1000, round_to_par=-place, played=18, has_score=True,
            running_place=place, grand_total=54)
        for p, place in zip(entrants, places)
    ]))


def test_a_played_play_in_promotes_its_actual_winners(tiny_world, fake_api):
    """Six spots, twelve entrants, and the play-in is over: the six who won it
    are at Worlds in every sim and the six who lost it are in none. Before
    this, the entry list was all the model read, so it re-ran the race on
    every refresh for the rest of the week."""
    entrants = [p for p, _, _ in tiny_world.players[10:22]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants)
    _play_in_played(fake_api, entrants, list(range(1, 13)))

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    worlds_ei = next(i for i, e in enumerate(res.events_meta)
                     if e["tid"] == config.TID_WORLDS)
    winners, losers = entrants[:6], entrants[6:]
    assert len(winners) == config.WORLDS_PLAYIN_SPOTS["MPO"]
    for pdga in winners:
        i = res.pdga_numbers.index(pdga)
        assert res.p_playin[i] == pytest.approx(1.0)
        assert res.att_probs[worlds_ei, i] == pytest.approx(1.0)
    for pdga in losers:
        i = res.pdga_numbers.index(pdga)
        assert res.p_playin[i] == pytest.approx(0.0)
        assert res.att_probs[worlds_ei, i] == pytest.approx(0.0)


def test_a_play_in_still_being_played_is_still_a_race(tiny_world, fake_api):
    """Mid-round the sheet already carries a RunningPlace, so reading the
    leaderboard alone would freeze the overnight leaders as the qualifiers.
    Only a finished event decides anything."""
    entrants = [p for p, _, _ in tiny_world.players[10:22]]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]],
                  playin_field=entrants)
    fake_api.event(config.TID_WORLDS_PLAYIN,
                   event_payload("Worlds Play-In", 1, [("MPO", 1)]))
    fake_api.round(config.TID_WORLDS_PLAYIN, "MPO", 1, round_payload([
        row(p, f"P{p}", 1000, round_to_par=-place, played=9, running_place=place)
        for place, p in enumerate(entrants, 1)
    ]))

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    p = np.array([res.p_playin[res.pdga_numbers.index(x)] for x in entrants])
    assert p.sum() == pytest.approx(config.WORLDS_PLAYIN_SPOTS["MPO"], abs=1e-9)
    assert np.all(p < 1.0)


def test_a_winner_already_in_the_worlds_roster_is_not_promoted_twice(tiny_world, fake_api):
    """The steady state a day later: PDGA has added the qualifiers to the
    Worlds field, so they read as directly qualified and the play-in has
    nobody left to promote. The losers must not inherit the vacated spots."""
    entrants = [p for p, _, _ in tiny_world.players[10:22]]
    winners = entrants[:6]
    _worlds_world(tiny_world, fake_api,
                  worlds_field=[p for p, _, _ in tiny_world.players[:10]] + winners,
                  playin_field=entrants)
    _play_in_played(fake_api, entrants, list(range(1, 13)))

    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    worlds_ei = next(i for i, e in enumerate(res.events_meta)
                     if e["tid"] == config.TID_WORLDS)
    for pdga in winners:
        i = res.pdga_numbers.index(pdga)
        assert res.att_probs[worlds_ei, i] == pytest.approx(1.0)
    for pdga in entrants:
        i = res.pdga_numbers.index(pdga)
        assert res.p_playin[i] == pytest.approx(0.0)
    for pdga in entrants[6:]:
        i = res.pdga_numbers.index(pdga)
        assert res.att_probs[worlds_ei, i] == pytest.approx(0.0)


def test_the_last_announced_wave_is_the_cut_the_model_uses():
    """The published phases and the modelled qualification line are the same
    rule stated twice — GMC's wave 2 and the MVP's tier 2 are exactly the
    points cuts, so a drift between them would silently mean the site is
    explaining a rule it does not simulate."""
    for key in ("gmc", "mvp"):
        invited = [ph for ph in config.REG_PHASES[key] if "top" in ph]
        assert invited[-1]["top"] == config.PLAYOFF_QUAL[key]["cut"]


def test_the_mvp_field_is_not_settled_until_gmc_has_been_played():
    """The MVP Open's last route in is won at GMC, not invited off a list.

    It has to be the final phase, and carry PLAYOFF_QUAL's own count, or
    _waves_all_open would call the roster final while the 8 MPO / 4 FPO spots
    that GMC decides are still outstanding — which switches off the
    GMC-performance path in simulate._simulate and zeroes out every player
    whose only road to the Cup runs through it.
    """
    phases = config.REG_PHASES["mvp"]
    perf = [ph for ph in phases if "perf" in ph]
    assert len(perf) == 1, "exactly one performance route into the MVP Open"
    assert perf[0]["perf"] == config.PLAYOFF_QUAL["mvp"]["perf"]
    assert phases[-1] is perf[0], "nothing can settle the field after it"


def test_an_event_page_list_never_closes_a_playoff_field(fake_api, fake_pages):
    """registered_roster falls back to the event page; registration_list must
    NOT inherit that. A page list is real but still growing, so returning it
    as staged would mark the field final the moment its waves opened and lock
    out everyone a later wave admits — the bubble _waves_all_open protects."""
    fake_pages.signups(config.TID_GMC, list(range(9001, 9021)))

    entries, staged = live_api.registration_list(config.TID_GMC, "MPO")
    assert set(entries) == set(range(9001, 9021))
    assert staged is False
    # ... while the roster read used for fields/attendance does see it
    assert set(live_api.registered_roster(config.TID_GMC, "MPO")) == set(range(9001, 9021))
