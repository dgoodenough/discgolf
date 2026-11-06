"""The Am planner's bundle (the pull itself needs PDGA credentials)."""
import datetime as dt

import pytest

from dgpt import amplan


def ev(**kw):
    base = {"tournament_id": "1", "tournament_name": "Test Open", "tier": "C", "class": "Pro-Am",
            "status": "sanctioned", "country": "United States", "city": "Emporia", "state_prov": "KS",
            "latitude": "38.4", "longitude": "-96.18", "start_date": "2026-06-27", "end_date": "2026-06-28",
            "registration_url": "https://example.test/reg"}
    return {**base, **kw}


@pytest.mark.parametrize("tier,groups", [
    ("C", ["C"]), ("C/B", ["C", "B"]), ("XC", ["X"]), ("XC/XB", ["X"]), ("M", ["M"]), ("ES", []), ("L", []),
])
def test_tier_groups(tier, groups):
    assert amplan.tier_groups(tier) == groups


def test_keep():
    assert amplan.keep(ev())
    assert amplan.keep(ev(**{"class": "Am"}))
    assert not amplan.keep(ev(**{"class": "Pro"}))
    assert not amplan.keep(ev(status="cancelled"))
    assert not amplan.keep(ev(tier="L"))
    assert not amplan.keep(ev(country="Finland"))
    assert not amplan.keep(ev(latitude="0", longitude="0"))


def test_bundle_rows():
    b = amplan.bundle([ev(), ev(tournament_id="2", **{"class": "Am"}, start_date="2027-03-06",
                               end_date="2027-03-07", city="Tulsa", state_prov="OK"),
                       ev(tournament_id="3", start_date="2025-12-01", end_date="2025-12-01")], [2026, 2027])
    F = {f: i for i, f in enumerate(b["meta"]["fields"])}
    assert [r[F["id"]] for r in b["events"]] == [1, 2]          # 2025 dropped, sorted by start
    r = b["events"][0]
    assert dt.date(1970, 1, 1) + dt.timedelta(days=r[F["start"]]) == dt.date(2026, 6, 27)
    assert r[F["am_only"]] == 0 and b["events"][1][F["am_only"]] == 1
    assert b["places"][r[F["place"]]] == "Emporia, KS"
    # weekend index: the Sunday nearest the last day, in weeks since the epoch
    sunday = dt.date(2026, 6, 28)
    assert r[F["wk"]] == (sunday - dt.date(1970, 1, 1)).days // 7
    assert b["meta"]["drive"]["b"] > 0


def test_build_falls_back_to_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(amplan, "CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(amplan, "BUNDLE", tmp_path / "amplan.json")
    (tmp_path / "cache.json").write_text('[{"tournament_id": "9", "tournament_name": "Cached", "tier": "B", '
                                         '"class": "Am", "status": "sanctioned", "country": "Canada", "city": "X", '
                                         '"state_prov": "ON", "latitude": "44", "longitude": "-79", '
                                         '"start_date": "2026-05-02", "end_date": "2026-05-03"}]')

    def boom(years):
        raise OSError("no network")
    monkeypatch.setattr(amplan, "pull", boom)
    msg = amplan.build([2026, 2027])
    assert "1 events" in msg and "cached" in msg
