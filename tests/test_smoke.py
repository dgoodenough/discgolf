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

from dgpt import export, invariants, movers, points, simulate, snapshot, standings

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


def test_roster_added_first_timer_gets_a_row(sim_result):
    world, _, res = sim_result
    assert world.newcomer in res.pdga_numbers
    ix = res.pdga_numbers.index(world.newcomer)
    assert res.current_points[ix] == 0.0


def test_export_bundle_is_well_formed(sim_result, tmp_path):
    world, _, res = sim_result
    export.export(res)
    bundle = json.loads((export.DOCS_DATA / "mpo.json").read_text(encoding="utf-8"))

    assert bundle["meta"]["division"] == "MPO"
    assert bundle["meta"]["cut"] == simulate.STANDINGS_CUT["MPO"]
    assert len(bundle["players"]) == world.expected_players
    assert len(bundle["cutline"]) == N_SIMS

    for p in bundle["players"]:
        for key in ("p_cut", "p_champ", "p_first", "p_gmc", "p_mvp"):
            assert 0.0 <= p[key] <= 1.0, f"{p['name']} {key}={p[key]}"
        assert len(p["hist"]) == simulate.MAX_HIST_RANK
        assert len(p["att"]) == len(bundle["events"])

    # remaining events: live + major + doubles + both playoffs (no Cup)
    assert len(bundle["events"]) == 5
    for ev in bundle["events"]:
        assert len(ev["curve"]) == 150

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

    # with a single snapshot neither window has anything to compare against
    movers.write_movers()
    out = json.loads(movers.OUT.read_text(encoding="utf-8"))
    empty = {"day": None, "week": None, "season": None}
    assert out == {"mpo": empty, "fpo": empty}


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
