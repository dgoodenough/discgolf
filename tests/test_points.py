"""Points-engine unit tests: tie averaging, Jomez bands, doubles transform,
and the 2026 per-class counting caps (the f1a83dd regression: caps are
per-class, not one pooled best-14)."""
from __future__ import annotations

import pytest

from dgpt import config, points


def test_tie_averaging_elite():
    # T2 pair spans places 2+3: (125 + 115) / 2
    assert points.assign_points([1, 2, 2, 4], "MPO", "elite") == [150.0, 120.0, 120.0, 105.0]


def test_class_multipliers_scale_the_base_curve():
    assert points.assign_points([1], "MPO", "elite_plus") == [pytest.approx(200.0)]
    assert points.assign_points([1], "MPO", "playoff") == [pytest.approx(250.0)]
    assert points.assign_points([1], "MPO", "major") == [pytest.approx(300.0)]


def test_jomez_bands_flat_no_tie_averaging():
    assert points.assign_points([1, 2, 2, 5, 6, 10, 11], "MPO", "jomez") == [
        20.0, 10.0, 10.0, 10.0, 5.0, 5.0, 0.0]


def test_doubles_curve_spans_singles_places():
    curve = points.event_curve("MPO", "doubles")
    assert curve[1] == pytest.approx((150.0 + 125.0) / 2)
    assert curve[2] == pytest.approx((115.0 + 105.0) / 2)


def test_season_total_per_class_caps():
    """Best 10 DGPT, best 2 majors, both playoffs, ALL Jomez bonus."""
    cls_by_tid = {}
    cls_by_tid.update({100 + i: "elite" for i in range(12)})       # 12 DGPT starts
    cls_by_tid.update({200: "major", 201: "major", 202: "major"})  # 3 majors
    cls_by_tid.update({300: "playoff", 301: "playoff"})
    cls_by_tid.update({400: "jomez", 401: "jomez"})
    cls_by_tid[500] = "championship"
    points._CLS_BY_TID.update(cls_by_tid)

    results = (
        [(100 + i, 100.0 - i) for i in range(12)]       # best 10 of 100..89
        + [(200, 300.0), (201, 250.0), (202, 200.0)]    # best 2 majors
        + [(300, 80.0), (301, 70.0)]                    # both playoffs count
        + [(400, 20.0), (401, 10.0)]                    # all Jomez counts
        + [(500, 999.0)]                                # championship: never counts
    )
    expected = sum(100.0 - i for i in range(10)) + 550.0 + 150.0 + 30.0
    assert points.season_total(results, "MPO") == pytest.approx(expected)
    assert config.COUNT_DGPT == 10 and config.COUNT_MAJOR == 2
