"""The drop-in kit — the summary the 2027 what-if tab computes against.

Nothing here can be checked against a real season, for the same reason
`test_project_2027.py` says: 2027 has not been played. What CAN be checked is
that the summary is a faithful reduction of the run it came out of, because
every quantity in it is a restatement of something the projection already
knows — the ladder is the standings it ranked, the curves are the points it
paid, the seed table is the one the Cup was played on. A kit that disagrees
with its own run would put a second, quieter model on the site.

The shape invariants matter more than they look. The client reconstructs a
season by reading every rung of the ladder at one uniform, and that only
produces a coherent standings table if the ladder is monotone in BOTH
directions: down the ranks at a fixed quantile, and across the quantiles at a
fixed rank. Break either and a player can be simultaneously 10th and 40th.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from dgpt import config, fields, points, project, season2027, whatif

N_SIMS = 400


# ------------------------------------------------------------ pure helpers

def test_ladder_ranks_are_dense_then_sparse():
    r = whatif.ladder_ranks(50, 689)
    assert r[:50] == list(range(1, 51)), "every rung down to the histogram's depth"
    assert r[-1] == 689, "and the last rung is the bottom of the roster"
    assert r == sorted(set(r)), "strictly increasing, no repeats"
    assert len(r) < 90, "the sparse tail is what keeps this a summary"
    # the sparse rungs spread geometrically, except the last, which is clamped
    # to the roster and so lands wherever the bottom of the table is
    gaps = [b - a for a, b in zip(r[49:], r[50:])][:-1]
    assert gaps == sorted(gaps)


def test_ladder_ranks_never_overrun_a_small_roster():
    """A division smaller than the histogram's depth still gets a whole ladder."""
    for n in (1, 2, 3, 12, 49):
        r = whatif.ladder_ranks(50, n)
        assert r == list(range(1, n + 1))


def test_field_bands_are_attendance_weighted():
    ratings = np.array([900.0, 1000.0, 1100.0])
    # only the middle player turns up: every band is theirs
    assert whatif.field_bands(ratings, np.array([0.0, 1.0, 0.0]), nbins=4) == [1000.0] * 4
    # nobody turns up: there is no field to describe, and no bands
    assert whatif.field_bands(ratings, np.array([0.0, 0.0, 0.0])) == []
    # weighting bites: a field that is mostly the 900 reads mostly 900
    bands = whatif.field_bands(ratings, np.array([0.9, 0.05, 0.05]), nbins=10)
    assert bands == sorted(bands), "bands run low to high"
    assert bands.count(900.0) >= 8


# ------------------------------------------------------------ the run's kit

@pytest.fixture
def world(monkeypatch, tmp_path):
    """A two-event 2026 season and a 40-player table, all local to the test.

    The same world `test_project_2027.py` runs its coherence checks in, kept
    here rather than shared so the two files can diverge: that one is about
    the projection, this one about the summary taken off it.
    """
    countries = {900_100 + i: (["FI", "SE", "EE", "NO"][i % 4] if i < 16 else "US")
                 for i in range(40)}
    table = [{"pdga_number": 900_100 + i, "name": f"Player {i:03d}", "rating": 1030 - i,
              "points": float(500 - i), "rank": i + 1, "starts": 2,
              "events": [(900001, 100.0, 1, "A"), (900002, 90.0, 2, "B")]}
             for i in range(40)]
    monkeypatch.setattr(fields, "load_countries", lambda: countries)
    monkeypatch.setattr(project, "DOCS_DATA", tmp_path / "data")
    sched = [
        {"tournament_id": 900001, "name": "A", "cls": "elite", "start_date": "2026-03-01",
         "end_date": "2026-03-03", "mpo": True, "fpo": True, "fpo_points": True, "completed": True},
        {"tournament_id": 900002, "name": "B", "cls": "elite", "start_date": "2026-04-01",
         "end_date": "2026-04-03", "mpo": True, "fpo": True, "fpo_points": True, "completed": True},
    ]
    return table, sched, countries, tmp_path / "data"


@pytest.fixture
def kits(world):
    """Both 2027 bundles, exported to a temp dir and read back as JSON.

    Read back rather than inspected in memory on purpose: the tab sees the
    file, so the tests do too — a value lost to rounding on the way out is a
    value the site never gets.
    """
    table, sched, _countries, out = world
    made = {}
    for spec in (season2027.DGPT, season2027.EUROTOUR):
        res = project.run(spec, "MPO", table, sched, n_sims=N_SIMS, chunk=100)
        project.export(res)
        made[spec.key] = json.loads((out / f"{spec.key}_mpo.json").read_text())
    return made


def test_only_the_tour_with_a_postseason_ships_a_kit(kits):
    """The what-if tab is about the Cup ladder, and the EuroTour has none."""
    assert "whatif" in kits["2027"]["meta"]
    assert "whatif" not in kits["et"]["meta"]
    assert all("field_rq" in e for e in kits["2027"]["schedule"] if e["ei"] is not None)
    assert all("field_rq" not in e for e in kits["et"]["schedule"])


def test_the_ladder_is_monotone_in_both_directions(kits):
    """The one property the client's reconstruction actually rests on.

    A season is rebuilt by reading every rank at a single uniform, so the
    ladder has to fall as rank deepens at every quantile, and rise with the
    quantile at every rank. Either violated and the same total ranks two ways.
    """
    w = kits["2027"]["meta"]["whatif"]
    L = np.array(w["ladder"], dtype=float)
    assert L.shape == (len(w["ranks"]), w["q"])
    assert np.all(np.diff(L, axis=0) <= 1e-9), "deeper rank, never more points"
    assert np.all(np.diff(L, axis=1) >= -1e-9), "higher quantile, never fewer points"


def test_the_gate_lines_sit_where_the_ladder_says(kits):
    """Both playoff lines are standings totals, and the second is the later one.

    The second playoff cuts a shallower field (72 v 120) after a whole extra
    event has paid out, so its line is above the first's in every season.
    """
    w = kits["2027"]["meta"]["whatif"]
    p1, p2 = np.array(w["play1_line"]), np.array(w["play2_line"])
    assert np.all(np.diff(p1) >= -1e-9) and np.all(np.diff(p2) >= -1e-9)
    assert np.all(p2 >= p1), "the second gate is never the looser one"


def test_the_cut_ladder_agrees_with_the_published_field(kits):
    """A coherence check against the rest of the bundle, not a restatement.

    The median total at the automatic-bid rank should be the points it takes
    to be roughly the `cut`-th best player — so about `cut` players should be
    projected above it. The two are different statistics (a median of order
    statistics against a table of means), so the band is generous; what it
    catches is an off-by-one or an inverted ladder, which would miss by a mile.
    """
    d = kits["2027"]
    m, w = d["meta"], d["meta"]["whatif"]
    at_cut = w["ladder"][w["ranks"].index(m["cut"])][w["q"] // 2]
    above = sum(1 for p in d["players"] if p["mean_pts"] >= at_cut)
    assert 0.6 * m["cut"] <= above <= 1.4 * m["cut"]


def test_the_performance_paths_are_probabilities_that_fall_with_place(kits):
    """P(the top finishers advance | you were one of them), measured by place.

    Thin bins deep in a field make this locally bumpy, so the claim tested is
    the one the client relies on: it is a probability, it is highest at the
    front, and it has run out by the back.

    A path can also be *uncontested*, and a flat zero is then the right answer
    rather than a bug. This world holds 40 players against a first playoff
    that fills to 120, so everybody in it qualifies for the second on points
    and nobody ever needs carrying — which is exactly the state a division
    smaller than the qualification constants puts the real tour in.
    """
    w = kits["2027"]["meta"]["whatif"]
    contested = 0
    for key in ("play2_perf", "champ_perf"):
        a = np.array(w[key], dtype=float)
        assert a.size > 1 and np.all((a >= 0.0) & (a <= 1.0)), key
        if a.max() == 0.0:
            continue
        contested += 1
        assert a[0] == pytest.approx(1.0), f"{key}: winning it always walks through"
        assert a[:5].mean() > a[-5:].mean(), key
    assert contested, "at least one Cup performance path has to be live here"


def test_the_kit_reuses_the_projection_s_own_tables(kits):
    """Points, seeds and invitations are read out of the run, not re-derived.

    This is the test that keeps the tab honest: if any of these forked, the
    what-if would quietly price a finish differently from the table above it.
    """
    w = kits["2027"]["meta"]["whatif"]
    assert w["cup_strokes"] == config.cup_start_strokes("MPO")
    assert w["invite"] == list(season2027.DGPT.invite_classes)

    # Every class on the calendar, not just one: three 2027 DGPT events are
    # `tour == "both"` and pay into the EuroTour ledger at a different value,
    # so a curve looked up off an event row rather than off the tour being
    # scored would price its whole class on the wrong table.
    # A hundredth of a point is what the curves are rounded to on the way out
    # (a DGPT+ floor of 1.3333 ships as 1.33), which over a whole season is
    # worth a fiftieth of a point and is the difference between a 7 KB block
    # and a 12 KB one.
    cent = pytest.approx(0, abs=0.005)
    for cls, shipped in w["curves"].items():
        assert len(shipped) == whatif.CURVE_DEPTH, cls
        if cls == "jomez":
            assert shipped[:12] == [points.jomez_bonus(p) for p in range(1, 13)]
            continue
        curve = points.event_curve("MPO", cls)
        deepest = max(curve)
        assert shipped[0] - curve[1] == cent, cls
        assert shipped[deepest - 1] - curve[deepest] == cent, cls
        assert shipped[-1] - curve[deepest] == cent, (
            f"{cls}: past the published table the floor keeps being paid — the "
            "same rule points.assign_points and export.curve_vector follow"
        )
    assert w["bands"] == whatif.BAND_N


def test_better_seeds_are_better_players(kits):
    """The Cup field a dropped-in player meets, by the seat its holder took."""
    w = kits["2027"]["meta"]["whatif"]
    cup = w["cup_field"]
    assert len(cup) == min(season2027.FIELD_SIZE["MPO"], len(kits["2027"]["players"]))
    assert cup[0] > cup[-1], "the No. 1 seed outrates the bottom one"


def test_the_kit_stays_a_summary(kits):
    """It is shipped to a phone on a tour-site connection; it has to stay small.

    The honest object is one season total per rank per simulated season, which
    at 20,000 seasons is megabytes. Everything in whatif.py exists to avoid
    shipping that, so a regression here is the whole design coming undone.
    """
    blob = json.dumps(kits["2027"]["meta"]["whatif"], separators=(",", ":"))
    assert len(blob) < 60_000, f"kit grew to {len(blob) // 1024} KB"


def test_a_division_smaller_than_its_own_cup_field_still_ships_a_kit(monkeypatch, tmp_path):
    """The qualification constants are written for a full tour (see whatif.Kit).

    Same shape `test_project_2027.test_small_division_still_projects` guards in
    the engine: a table narrower than FIELD_SIZE, PLAYOFF_QUAL or the
    histogram's depth, which every one of the kit's arrays is sized against.
    """
    countries: dict[int, str] = {}
    table = [{"pdga_number": 900_500 + i, "name": f"P{i}", "rating": 1000 - i,
              "points": float(9 - i), "rank": i + 1, "starts": 1,
              "events": [(900001, 100.0, 1, "A")]} for i in range(5)]
    monkeypatch.setattr(fields, "load_countries", lambda: countries)
    monkeypatch.setattr(project, "DOCS_DATA", tmp_path / "data")
    sched = [{"tournament_id": 900001, "name": "A", "cls": "elite",
              "start_date": "2026-03-01", "end_date": "2026-03-03", "mpo": True,
              "fpo": True, "fpo_points": True, "completed": True}]
    res = project.run(season2027.DGPT, "FPO", table, sched, n_sims=120, chunk=60)
    project.export(res)
    w = json.loads((tmp_path / "data" / "2027_fpo.json").read_text())["meta"]["whatif"]
    assert w["ranks"] == [1, 2, 3, 4, 5]
    assert len(w["cup_field"]) == 5, "the Cup field cannot be wider than the table"
    assert np.all(np.diff(np.array(w["ladder"], dtype=float), axis=0) <= 1e-9)
