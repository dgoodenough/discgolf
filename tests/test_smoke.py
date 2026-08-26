"""End-to-end smoke: the real pipeline over the tiny world, network mocked.

Runs everything a production refresh runs below schedule.build() — standings,
simulate.run (the c287e19 shadowing crash lived at its entry), export,
snapshot, movers, invariants — and asserts structural truths of the output
rather than exact odds.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from dgpt import export, invariants, movers, points, schedule, simulate, snapshot, standings
from tests.conftest import event_payload, round_payload, row

N_SIMS = 400


@pytest.fixture
def sim_result(tiny_world):
    table = standings.compute("MPO")
    standings.write_csv("MPO", table)
    res = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    return tiny_world, table, res


def test_standings_from_completed_events(tiny_world):
    table = standings.compute("MPO")
    by_pdga = {r["pdga_number"]: r for r in table}

    # star won the elite event (150) and the elite+ event (150 * 4/3)
    assert by_pdga[tiny_world.star]["points"] == pytest.approx(350.0)
    assert table[0]["pdga_number"] == tiny_world.star

    # the T2 pair split places 2+3 of the elite curve: (125 + 115) / 2 = 120
    assert by_pdga[5002]["events"][0][1] == pytest.approx(120.0)

    # the DNF player posted no round-2 score: no points, no standings row
    assert tiny_world.dnf not in by_pdga
    assert len(table) == 30


def test_simulation_probabilities_are_coherent(sim_result):
    world, table, res = sim_result
    n = len(res.names)
    assert n == world.expected_players

    for arr in (res.p_cut, res.p_field, res.p_first, res.p_champ,
                res.p_gmc, res.p_mvp, res.p_gmc_field, res.p_mvp_field,
                res.p_mvp_qual):
        assert np.all((arr >= 0.0) & (arr <= 1.0))
        assert np.all(np.isfinite(arr))

    # exactly one No. 1 seed per sim
    assert res.p_first.sum() == pytest.approx(1.0)
    # the championship field contains every auto-bid
    assert np.all(res.p_champ >= res.p_cut - 1e-12)
    # the star banked an event win: the Cup special invite is a lock
    star_ix = res.pdga_numbers.index(world.star)
    assert res.p_champ[star_ix] == pytest.approx(1.0)
    # mean final rank is a permutation average: within [1, n]
    assert np.all((res.mean_rank >= 1.0) & (res.mean_rank <= n))


def test_live_event_is_modeled_from_current_scores(sim_result):
    world, _, res = sim_result
    assert world.live_tid in res.live_stats
    per = res.live_stats[world.live_tid]
    assert len(per) == world.live_field_size
    star = per[world.star]
    assert star["cur"] == pytest.approx(world.live_leader_cur)
    assert 0.0 <= star["win"] <= 1.0
    # the leader must be likelier to win than the trailing player
    worst = max(per.values(), key=lambda s: s["cur"])
    assert star["win"] > worst["win"]


def test_live_event_decided_in_a_playoff_is_not_a_coin_flip(tiny_world, fake_api):
    """A playoff is scored on its own sheet and its strokes never reach anyone's
    total, so the players who went to it stay level on score with no holes
    left. Ranked on score alone the winner and the runner-up split the win
    50/50 after the event has been decided; the sheet's RunningPlace resolves
    it, and the ranking has to follow it (see simulate.TIE_EPS)."""
    world = tiny_world
    players = world.players[:20]
    # two players tie the tournament on score; the sheet ranks them 1 and 2
    totals = {p: (-30.0 if i < 2 else -25.0 + i) for i, (p, _, _) in enumerate(players)}
    order = sorted(totals, key=lambda p: totals[p])
    place = {p: i for i, p in enumerate(order, 1)}
    winner, runner_up = order[0], order[1]

    fake_api.event(900003, event_payload("Test Live Open", 3, [("MPO", 3)]))
    for rnd in (1, 2, 3):
        fake_api.round(900003, "MPO", rnd, round_payload([
            row(p, n, r, round_to_par=totals[p] / 3, played=18, has_score=True,
                running_place=place[p])
            for p, n, r in players
        ]))

    per = simulate.run("MPO", n_sims=N_SIMS, chunk=200).live_stats[world.live_tid]
    assert per[winner]["cur"] == per[runner_up]["cur"]   # level on score
    assert per[winner]["rem"] == 0.0
    assert per[winner]["win"] == 1.0
    assert per[runner_up]["win"] == 0.0


def test_roster_added_first_timer_gets_a_row(sim_result):
    world, _, res = sim_result
    assert world.newcomer in res.pdga_numbers
    ix = res.pdga_numbers.index(world.newcomer)
    assert res.current_points[ix] == 0.0


def test_export_bundle_is_well_formed(sim_result, tmp_path):
    world, _, res = sim_result
    export.export(res)
    raw = (export.DOCS_DATA / "mpo.json").read_text(encoding="utf-8")
    # Strict grammar: json.loads accepts bare NaN/Infinity but JSON.parse
    # rejects them, and one anywhere in the bundle costs the browser the page.
    bundle = json.loads(raw, parse_constant=lambda c: pytest.fail(
        f"non-standard JSON constant {c!r} in the exported bundle"
    ))

    assert bundle["meta"]["division"] == "MPO"
    assert bundle["meta"]["cut"] == simulate.STANDINGS_CUT["MPO"]
    assert len(bundle["players"]) == world.expected_players
    assert len(bundle["cutline"]) == N_SIMS

    for p in bundle["players"]:
        for key in ("p_cut", "p_champ", "p_first", "p_gmc", "p_mvp"):
            assert 0.0 <= p[key] <= 1.0, f"{p['name']} {key}={p[key]}"
        assert len(p["hist"]) == simulate.MAX_HIST_RANK
        assert len(p["att"]) == len(bundle["events"])

    # every schedule row carries the backend's own "in progress" answer, so the
    # page never re-derives that window from a UTC date (which has no grace day
    # and reads a US Sunday finish as over while scores are still moving)
    live_tids = {r["tournament_id"] for r in schedule.live_events()}
    assert {s["tid"] for s in bundle["schedule"] if s["live"]} == live_tids
    assert world.live_tid in live_tids

    # remaining events: live + major + doubles + both playoffs (no Cup)
    assert len(bundle["events"]) == 5
    for ev in bundle["events"]:
        assert len(ev["curve"]) == export.CURVE_DEPTH

    star_row = next(p for p in bundle["players"] if p["pdga"] == world.star)
    assert star_row["p_champ"] == 1.0
    assert star_row["live"]  # projected finish for the live event is attached


def test_snapshot_and_movers_run_clean(sim_result):
    world, _, res = sim_result
    export.export(res)
    msg = snapshot.record(res, "MPO")
    assert "recorded snapshot" in msg
    assert (snapshot.SNAP_DIR / "history_mpo.csv").exists()

    # a second record on the same day is a no-op, not a duplicate
    assert "already taken today" in snapshot.record(res, "MPO")

    # with a single snapshot neither snapshot-diff window has anything to
    # compare against; the season window is calendar-dependent (it replays
    # banked results as of the previous month's end, no snapshots needed),
    # so it may or may not materialize depending on where today falls
    # relative to the fixture's event dates
    movers.write_movers()
    out = json.loads(movers.OUT.read_text(encoding="utf-8"))
    for div in ("mpo", "fpo"):
        assert out[div]["day"] is None
        assert out[div]["week"] is None
        season = out[div]["season"]
        assert season is None or season["metric"] == "rank"


def test_invariants_clean_on_tiny_world(sim_result):
    world, _, res = sim_result
    assert invariants.run_checks() == []
    assert invariants.MARKER.exists()
    assert invariants.MARKER.read_text(encoding="utf-8") == ""


def test_full_refresh_sequence(tiny_world):
    """The exact call sequence refresh.main() performs below schedule.build().
    This is the test that would have caught the module-shadowing crash: it
    enters simulate.run for real."""
    points.refresh_classes()
    table = standings.compute("MPO")
    standings.write_csv("MPO", table)
    res = simulate.run("MPO", n_sims=200, chunk=100)
    simulate.write_csv(res)
    export.export(res)
    snapshot.record(res, "MPO")
    movers.write_movers()
    invariants.run_checks()


def test_live_stats_carry_current_place_and_pre_event_baseline(sim_result):
    """The day tracker's columns: current standing from the sheet's own
    RunningPlace, and the pre-event expectation the live projection is
    compared against."""
    world, _, res = sim_result
    live = res.live_stats.get(world.live_tid) or {}
    assert live, "tiny world should have a live event"
    row = next(iter(live.values()))
    for key in ("cur", "rem", "thru", "place", "mean_pts", "pre_pts", "pre_place"):
        assert key in row, f"live stats missing {key}"
    assert row["pre_place"] >= 1
    assert row["pre_pts"] >= 0
    # tiny world: R1 complete, R2 nine holes in for everyone still playing
    assert {s["thru"] for s in live.values()} == {18, 27}
