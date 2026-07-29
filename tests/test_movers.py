"""Window anchoring, sparkline construction, and the live day endpoint.

Snapshot history is a plain CSV and the app bundle is plain JSON, so these
build a synthetic season directly rather than needing captured payloads —
which lets each anchoring rule be exercised against a known calendar.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

import pytest

from dgpt import config, movers

FIELDS = [
    "snapshot_date", "taken_at", "events_completed", "division", "pdga_number",
    "name", "rating", "cur_rank", "cur_points", "p_champ", "p_cut", "p_gmc",
    "p_mvp", "p_mvp_qual", "p_first", "mean_pts", "mean_rank", "registered",
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Repoint movers at a tmp repo and return a builder for its inputs."""
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(movers, "OUT", tmp_path / "docs" / "data" / "movers.json")
    monkeypatch.setattr(movers, "APP_DATA", tmp_path / "docs" / "data")
    (tmp_path / "docs" / "data").mkdir(parents=True)
    (tmp_path / "predictions").mkdir()

    def build(history: dict[str, dict[int, float]], *, live: dict[int, float],
              players=(1, 2), ratings=None, registered="", events=(), schedule=()):
        """history: {date: {pdga: p_champ}}; live: {pdga: p_champ}."""
        rows = []
        for date, vals in sorted(history.items()):
            for pdga, p in vals.items():
                rows.append({
                    **{k: "" for k in FIELDS},
                    "snapshot_date": date, "taken_at": f"{date}T00:00:00",
                    "division": "MPO", "pdga_number": pdga, "name": f"P{pdga}",
                    "rating": (ratings or {}).get(pdga, 1000), "cur_rank": pdga,
                    "p_champ": p, "registered": registered,
                })
        path = tmp_path / "predictions" / "history_mpo.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        bundle = {
            "schedule": list(schedule),
            "events": [{"tid": t} for t in events],
            "players": [
                {"pdga": p, "name": f"P{p}", "rank": p, "rating": (ratings or {}).get(p, 1000),
                 "p_champ": live[p], "att": [1.0] * len(events), "banked": []}
                for p in players if p in live
            ],
        }
        (tmp_path / "docs" / "data" / "mpo.json").write_text(json.dumps(bundle), encoding="utf-8")
        (tmp_path / "docs" / "data" / "fpo.json").write_text(json.dumps(bundle), encoding="utf-8")
        return path

    return build


def _run(monkeypatch, today: dt.date) -> dict:
    real = dt.date

    class Frozen(real):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(movers.dt, "date", Frozen)
    movers.write_movers()
    monkeypatch.setattr(movers.dt, "date", real)
    return json.loads(movers.OUT.read_text(encoding="utf-8"))


def test_day_window_uses_live_bundle_not_todays_snapshot(world, monkeypatch):
    """The whole point of the day tab: during an event, today's snapshot was
    written by the first refresh of the morning, before play. The comparison
    must be against the live bundle instead."""
    world(
        {"2026-07-26": {1: 0.40}, "2026-07-27": {1: 0.41}},  # today's snapshot: stale
        live={1: 0.75},                                       # current: play happened
    )
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    day = out["mpo"]["day"]
    assert day["baseline"] == "2026-07-26"      # yesterday, not today
    assert day["live_latest"] is True
    m = day["movers"][0]
    assert m["champ_from"] == 0.40 and m["champ_to"] == 0.75
    assert m["spark"][-1] == 0.75               # last point is the live value


def test_week_window_anchors_to_mondays_and_is_stable(world, monkeypatch):
    hist = {
        "2026-07-13": {1: 0.20},  # prior Monday
        "2026-07-17": {1: 0.30},  # midweek — must not become an endpoint
        "2026-07-20": {1: 0.55},  # this Monday
        "2026-07-23": {1: 0.90},  # after the window
    }
    world(hist, live={1: 0.95})
    for day in (20, 22, 26):  # Mon through Sun of the same week
        out = _run(monkeypatch, dt.date(2026, 7, day))
        wk = out["mpo"]["week"]
        assert (wk["baseline"], wk["latest"]) == ("2026-07-13", "2026-07-20")
        assert wk["movers"][0]["champ_to"] == 0.55  # not the live 0.95
    # next Monday rolls the window forward
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    assert out["mpo"]["week"]["baseline"] == "2026-07-20"


def test_spark_gaps_forward_fill_but_pre_history_stays_null(world, monkeypatch):
    """A missing snapshot means 'predictions unchanged', so forward-fill is
    exact. An axis point BEFORE any history is a real gap and must stay null —
    it must not fall through to the live value."""
    world({"2026-07-25": {1: 0.50}, "2026-07-26": {1: 0.60}}, live={1: 0.70})
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    spark = out["mpo"]["day"]["movers"][0]["spark"]
    axis = out["mpo"]["day"]["spark_dates"]
    assert axis == [f"2026-07-{d}" for d in (21, 22, 23, 24, 25, 26, 27)]
    assert spark[:4] == [None] * 4          # before history began
    assert spark[4] == 0.50                 # 7/25 snapshot
    assert spark[5] == 0.60                 # 7/26 snapshot
    assert spark[6] == 0.70                 # today = live
    # a gap between snapshots forward-fills (7/23-7/25 carry 7/22's value)
    world({"2026-07-22": {1: 0.50}, "2026-07-26": {1: 0.60}}, live={1: 0.75})
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    assert out["mpo"]["day"]["movers"][0]["spark"] == [None, 0.50, 0.50, 0.50, 0.50, 0.60, 0.75]


def test_per_window_noise_floors(world, monkeypatch):
    """1.5% moves the day tab but not the week tab."""
    world(
        {"2026-07-20": {1: 0.500, 2: 0.500}, "2026-07-26": {1: 0.500, 2: 0.500}},
        live={1: 0.515, 2: 0.500}, players=(1, 2),
    )
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    assert [m["pdga"] for m in out["mpo"]["day"]["movers"]] == [1]
    assert out["mpo"]["week"]["movers"] == []   # same data, above the day floor only


def test_day_window_needs_a_prior_snapshot(world, monkeypatch):
    world({"2026-07-27": {1: 0.50}}, live={1: 0.90})
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    assert out["mpo"]["day"] is None            # nothing to compare against
    assert out["mpo"]["week"] is None


def test_completed_and_gated_events_excluded_from_reg_changes(world, monkeypatch):
    """Regression for the two false-alarm classes: a completed event and a
    standings-gated playoff both leave the registered set without being
    de-registrations."""
    sched = [
        {"tid": 10, "end": "2026-07-26", "completed": True, "cls": "major", "name": "Major"},
        {"tid": 20, "end": "2026-09-20", "completed": False, "cls": "playoff", "name": "GMC"},
        {"tid": 30, "end": "2026-08-02", "completed": False, "cls": "elite_plus", "name": "Ledge"},
    ]
    build = world
    build({"2026-07-26": {1: 0.40}}, live={1: 0.60}, registered="10;20;30",
          events=(), schedule=sched)
    # live bundle has no events => current registered set is empty => every tid
    # "left" the set; only the genuine upcoming one may be reported
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    m = out["mpo"]["day"]["movers"][0]
    assert m["reg_removed"] == [30]
