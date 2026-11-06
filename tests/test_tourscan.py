"""The full-scale scan's helpers (the pull itself needs PDGA credentials)."""
import datetime as dt

import numpy as np
import pytest

from dgpt import tourscan


@pytest.mark.parametrize("day,sunday", [
    ("2027-01-03", "2027-01-03"),   # Sunday
    ("2027-01-09", "2027-01-10"),   # Saturday -> next day
    ("2027-01-11", "2027-01-10"),   # holiday Monday -> day before
    ("2027-01-06", "2027-01-03"),   # Wednesday -> nearer Sunday behind
])
def test_weekend(day, sunday):
    assert tourscan.weekend(dt.date.fromisoformat(day)) == dt.date.fromisoformat(sunday)


def test_coords_and_amateur():
    assert tourscan.coords({"latitude": "35.2", "longitude": "-80.8"}) == (35.2, -80.8)
    assert tourscan.coords({"latitude": "0", "longitude": "0"}) is None
    assert tourscan.coords({"latitude": ""}) is None
    assert tourscan.is_amateur({"class": "Pro/Am"}) and tourscan.is_amateur({"class": "Am"})
    assert not tourscan.is_amateur({"class": "Pro"})


def _ev(i, wk, lat, lon):
    s = dt.date(2027, 1, 1).toordinal() + 7 * wk
    return {"lat": lat, "lon": lon, "s": s, "f": s + 2, "wk": wk}


def test_pruning_never_beats_the_full_search():
    rng = np.random.default_rng(0)
    ev = [_ev(i, i % 8, 35 + rng.uniform(-3, 3), -85 + rng.uniform(-3, 3)) for i in range(80)]
    est = lambda km: 50 + 0.8 * km  # noqa: E731
    for style in ("hub", "road"):
        full = tourscan.best_trip((35, -85), ev, 3, 4, style, est)
        assert np.isfinite(full)
        for K in (1, 3, 10):
            assert tourscan.best_trip((35, -85), ev, 3, 4, style, est, K=K) >= full - 1e-9
        assert tourscan.best_trip((35, -85), ev, 3, 4, style, est, K=100) == pytest.approx(full)
