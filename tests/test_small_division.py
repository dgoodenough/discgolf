"""Divisions smaller than the qualification constants, and empty ones.

FPO's rules admit 4 players on the MVP-performance path and seat 18 by
points. Nothing guaranteed the division actually held that many: the
performance paths index into the player axis with np.partition, so a field
below the constant raised `IndexError: index 3 is out of bounds for axis 1
with size 0` from inside numpy, with nothing naming the division or the rule
that overran.
"""
from __future__ import annotations

import json

import pytest

from dgpt import export, ratings, simulate
from .conftest import round_payload, row

FPO_PLAYERS = [(6001, "Fpo One", 950), (6002, "Fpo Two", 940), (6003, "Fpo Three", 930)]


@pytest.fixture
def small_fpo(tiny_world, fake_api):
    """Three FPO players registered for the future major, no starts yet.

    Three is below every FPO constant that indexes the player axis: the
    standings cut (18), the MVP-performance slots (4). It is above
    perf_champ (2), so the championship path still selects a real subset.
    """
    ratings._memo = None
    existing = json.loads(ratings.RATINGS_FILE.read_text(encoding="utf-8"))
    existing["FPO"] = {str(p): r for p, _, r in FPO_PLAYERS}
    ratings.RATINGS_FILE.write_text(json.dumps(existing), encoding="utf-8")
    fake_api.round(900004, "FPO", 1, round_payload(
        [row(p, n, r, played=0) for p, n, r in FPO_PLAYERS]
    ))
    return tiny_world


def test_division_smaller_than_its_cuts_simulates(small_fpo):
    res = simulate.run("FPO", n_sims=200, chunk=80)

    assert sorted(res.pdga_numbers) == [p for p, _, _ in FPO_PLAYERS]
    # fewer players than qualifying spots: everyone is in, every sim
    assert res.p_cut.tolist() == [1.0, 1.0, 1.0]
    assert res.p_champ.tolist() == [1.0, 1.0, 1.0]
    # ranks are still a permutation of 1..3
    assert res.mean_rank.min() >= 1.0 and res.mean_rank.max() <= 3.0
    assert abs(float(res.p_first.sum()) - 1.0) < 1e-9


def test_cutline_reports_the_last_filled_spot_when_the_field_is_short(small_fpo):
    res = simulate.run("FPO", n_sims=200, chunk=80)

    # 3 players for 18 spots: the "last qualifying spot" is the lowest-ranked
    # player actually present, and no spot exists outside the cut.
    assert res.cutline.min() >= 0.0
    assert res.cutline2.tolist() == [0.0] * 200


def test_export_of_a_short_field_is_parseable_by_a_browser(small_fpo):
    res = simulate.run("FPO", n_sims=200, chunk=80)
    export.export(res)
    raw = (export.DOCS_DATA / "fpo.json").read_text(encoding="utf-8")

    # An event nobody is projected to play used to take nanstd over an all-NaN
    # slice and ship `"opp_score_sd": NaN`. json.loads accepts that; JSON.parse
    # throws, and one of them anywhere in the bundle costs the browser the
    # whole page — so assert the strict grammar, not merely that Python reads it.
    json.loads(raw, parse_constant=lambda c: pytest.fail(
        f"non-standard JSON constant {c!r} in the exported bundle"
    ))
    assert all(ev["opp_score_sd"] == 0.0 for ev in res.events_meta if ev["field_size"] == 0.0)


def test_top_k_by_place_admits_everyone_when_short(small_fpo):
    import numpy as np

    # k above the player count must not index past the axis, and must admit
    # every eligible player rather than an arbitrary subset.
    place = np.array([[1, 2, 3], [3, 1, 2]])
    eligible = np.array([[True, True, False], [True, False, True]])
    got = simulate._top_k_by_place(place, eligible, k=9, n=3)
    assert got.tolist() == eligible.tolist()


def test_empty_division_fails_by_name_not_indexerror(tiny_world):
    # tiny_world has no FPO ratings and no FPO rosters at all.
    with pytest.raises(ValueError, match=r"FPO: no players with a known rating"):
        simulate.run("FPO", n_sims=50, chunk=50)
