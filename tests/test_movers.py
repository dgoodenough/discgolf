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

from dgpt import config, movers, snapshot

FIELDS = [
    "snapshot_date", "taken_at", "events_completed", "division", "pdga_number",
    "name", "rating", "cur_rank", "cur_points", "p_champ", "p_cut", "p_gmc",
    "p_mvp", "p_mvp_qual", "p_first", "mean_pts", "mean_rank", "registered",
    "signed",
]
# The snapshot schema is this module's input contract, and the two silently
# drifted apart once already (a column added there, the writer here still on
# the old list, every test failing on a csv fieldname error that named the
# symptom and not the cause).
assert FIELDS == snapshot.FIELDS, "test schema drifted from snapshot.FIELDS"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Repoint movers at a tmp repo and return a builder for its inputs."""
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(movers, "OUT", tmp_path / "docs" / "data" / "movers.json")
    monkeypatch.setattr(movers, "APP_DATA", tmp_path / "docs" / "data")
    (tmp_path / "docs" / "data").mkdir(parents=True)
    (tmp_path / "predictions").mkdir()

    def build(history: dict[str, dict[int, float]], *, live: dict[int, float],
              players=(1, 2), ratings=None, registered="", events=(), schedule=(),
              banked=None, ranks=None, meta=None, mranks=None, mrank_live=None,
              signed="", signed_live=None):
        """history: {date: {pdga: p_champ}}; live: {pdga: p_champ}.
        mranks: {date: {pdga: mean_rank}}; mrank_live: {pdga: mean_rank}."""
        rows = []
        for date, vals in sorted(history.items()):
            for pdga, p in vals.items():
                rows.append({
                    **{k: "" for k in FIELDS},
                    "snapshot_date": date, "taken_at": f"{date}T00:00:00",
                    "division": "MPO", "pdga_number": pdga, "name": f"P{pdga}",
                    "rating": (ratings or {}).get(pdga, 1000), "cur_rank": pdga,
                    "p_champ": p, "registered": registered, "signed": signed,
                    "mean_rank": (mranks or {}).get(date, {}).get(pdga, ""),
                })
        path = tmp_path / "predictions" / "history_mpo.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        bundle = {
            "meta": meta or {"cut": 28, "gmc_cut": 100},
            "schedule": list(schedule),
            "events": [{"tid": t} for t in events],
            "players": [
                {"pdga": p, "name": f"P{p}", "rank": (ranks or {}).get(p, p),
                 "rating": (ratings or {}).get(p, 1000),
                 "p_champ": live[p], "att": [1.0] * len(events),
                 **((signed_live or {}).get(p) or {}),
                 "mean_rank": (mrank_live or {}).get(p, ""),
                 "banked": list((banked or {}).get(p, []))}
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


# ---------------------------------------------------------------- season tab

def _sched(*specs):
    return [{"tid": t, "end": end, "completed": True, "cls": "elite", "name": f"E{t}"}
            for t, end in specs]


def test_rank_asof_replays_standings_from_banked_results(world, monkeypatch):
    """The season tab's foundation: filtering banked events by end date and
    re-running the capping logic reproduces the standings of that date."""
    from dgpt import movers as M
    bundle = {
        "meta": {"cut": 28},
        "schedule": _sched((1, "2026-03-01"), (2, "2026-06-15")),
        "events": [],
        "players": [
            {"pdga": 10, "name": "Early", "rank": 2, "rating": 1000, "p_champ": 0.1, "att": [],
             "banked": [{"tid": 1, "pts": 100.0, "place": 3}]},
            {"pdga": 11, "name": "Late", "rank": 1, "rating": 1000, "p_champ": 0.2, "att": [],
             "banked": [{"tid": 2, "pts": 150.0, "place": 1}]},
        ],
    }
    # in March only the first event counts, so Early leads and Late is unranked
    assert M._rank_asof(bundle, "MPO", "2026-03-31") == {10: 1}
    # by July both count and Late is ahead
    assert M._rank_asof(bundle, "MPO", "2026-07-01") == {11: 1, 10: 2}


def test_season_window_is_rank_based_and_needs_no_snapshots(world, monkeypatch):
    """Season movement is measured in places and reconstructed from results, so
    it works with no usable snapshot history at all."""
    banked = {
        1: [{"tid": 1, "pts": 100.0, "place": 3}, {"tid": 2, "pts": 90.0, "place": 5}],   # gained in July
        2: [{"tid": 1, "pts": 95.0, "place": 4}],
    }
    world({"2026-07-29": {1: 0.5, 2: 0.5}}, live={1: 0.6, 2: 0.4}, players=(1, 2),
          ranks={1: 1, 2: 2}, banked=banked,
          schedule=_sched((1, "2026-05-01"), (2, "2026-07-15")))
    out = _run(monkeypatch, dt.date(2026, 7, 29))
    s = out["mpo"]["season"]
    assert s["metric"] == "rank" and s["baseline"] == "2026-07-01"
    # P1 was 2nd at the end of June (95 vs 100), 1st now: +1 place... below the
    # 3-place floor, so nobody qualifies — the floor is doing its job
    assert s["movers"] == []
    # the axis still spans the season and ends today
    assert s["spark_dates"][-1] == "2026-07-29"
    assert len(s["spark_dates"]) == 7  # Jan..Jun month ends + today


def test_season_window_filters_to_the_cup_bubble(world, monkeypatch):
    """A big climb deep in the field is a field-filling artifact, not a story:
    only players within 2x the auto-bid cut are eligible."""
    banked = {
        1: [{"tid": 1, "pts": 500.0, "place": 1}, {"tid": 2, "pts": 300.0, "place": 2}],   # contender, climbs
        2: [{"tid": 2, "pts": 1.0, "place": 90}],                               # deep field, huge climb
    }
    # pad so ranks are meaningful: player 2 sits far outside the bubble
    world({"2026-07-29": {1: 0.5, 2: 0.01}}, live={1: 0.5, 2: 0.01}, players=(1, 2),
          ranks={1: 5, 2: 400}, banked=banked, meta={"cut": 28},
          schedule=_sched((1, "2026-05-01"), (2, "2026-07-15")))
    out = _run(monkeypatch, dt.date(2026, 7, 29))
    shown = {m["pdga"] for m in out["mpo"]["season"]["movers"]}
    assert 2 not in shown  # rank 400 is outside 2x28, however far it moved


def test_locked_players_seed_battle_qualifies_without_an_odds_move(world, monkeypatch):
    """Two locks (p_champ 1.0 on both endpoints, odds delta 0) racing for the
    No. 1 seed: the projected-seed move alone must surface the mover, carrying
    the proj_rank fields the panel renders."""
    world(
        {"2026-07-26": {1: 1.0, 2: 1.0}, "2026-07-27": {1: 1.0, 2: 1.0}},
        live={1: 1.0, 2: 1.0},
        mranks={"2026-07-26": {1: 3.4, 2: 1.2}, "2026-07-27": {1: 3.4, 2: 1.2}},
        mrank_live={1: 1.1, 2: 3.5},
    )
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    day = out["mpo"]["day"]
    by = {m["pdga"]: m for m in day["movers"]}
    assert set(by) == {1, 2}
    assert by[1]["proj_rank_from"] == 3.4 and by[1]["proj_rank_to"] == 1.1
    assert by[1]["proj_rank_delta"] == pytest.approx(-2.3)
    assert by[1]["delta"] == 0.0


def test_sub_floor_seed_wiggle_does_not_qualify(world, monkeypatch):
    """Daily mean_rank jitter under the measured floor stays invisible."""
    world(
        {"2026-07-26": {1: 1.0}, "2026-07-27": {1: 1.0}},
        live={1: 1.0},
        mranks={"2026-07-26": {1: 2.0}, "2026-07-27": {1: 2.0}},
        mrank_live={1: 3.0},  # |delta| = 1.0 < MIN_RANK_DELTA
    )
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    assert out["mpo"]["day"]["movers"] == []


def test_missing_mean_rank_still_movers_on_odds_with_null_seed_fields(world, monkeypatch):
    """Pre-schema snapshots have no mean_rank recorded for comparison shape —
    an odds mover still appears, with the seed columns null, never fabricated."""
    world(
        {"2026-07-26": {1: 0.30}, "2026-07-27": {1: 0.31}},
        live={1: 0.60},
        mrank_live={1: 12.0},  # live has one, the baseline doesn't
    )
    out = _run(monkeypatch, dt.date(2026, 7, 27))
    m = out["mpo"]["day"]["movers"][0]
    assert m["delta"] == pytest.approx(0.30)
    assert m["proj_rank_from"] is None and m["proj_rank_delta"] is None
    assert m["proj_rank_to"] == 12.0


# ---------------------------------------------- playoff sign-ups vs the gate

GMC_SCHED = [{"tid": config.TID_GMC, "end": "2026-09-20", "completed": False,
              "cls": "playoff", "name": "GMC"},
             {"tid": config.TID_MVP, "end": "2026-09-27", "completed": False,
              "cls": "playoff", "name": "MVP"}]


def test_playoff_signup_shows_as_a_registration_change(world, monkeypatch):
    """A player entering GMC is a registration fact — unpredictable, and the
    reason the column exists. It must show even though the playoff class is
    excluded from the attendance-based signal."""
    world({"2026-07-26": {1: 0.40}}, live={1: 0.60}, schedule=GMC_SCHED,
          signed="-",                                     # entered nothing before
          signed_live={1: {"reg_gmc": 1, "reg_mvp": 0}})  # entered GMC since

    m = _run(monkeypatch, dt.date(2026, 7, 27))["mpo"]["day"]["movers"][0]
    assert m["reg_added"] == [config.TID_GMC]
    assert m["reg_removed"] == []


def test_playoff_withdrawal_shows_too(world, monkeypatch):
    world({"2026-07-26": {1: 0.60}}, live={1: 0.40}, schedule=GMC_SCHED,
          signed=f"{config.TID_GMC};{config.TID_MVP}",
          signed_live={1: {"reg_gmc": 1, "reg_mvp": 0}})

    m = _run(monkeypatch, dt.date(2026, 7, 27))["mpo"]["day"]["movers"][0]
    assert m["reg_removed"] == [config.TID_MVP]


def test_crossing_the_qualification_cutline_is_not_a_signup(world, monkeypatch):
    """The other half of the distinction. Attendance at a playoff event is the
    signup list unioned with the standings gate, so a player crossing it may
    simply have climbed into the top 100 — a qualification swing the GMC/MVP
    column already reports, and not registration churn."""
    world({"2026-07-26": {1: 0.40}}, live={1: 0.60}, registered="",
          events=(config.TID_GMC,), schedule=GMC_SCHED,
          signed="-", signed_live={1: {"reg_gmc": 0, "reg_mvp": 0}})

    m = _run(monkeypatch, dt.date(2026, 7, 27))["mpo"]["day"]["movers"][0]
    assert m["reg_added"] == []     # att crossed to 1.0, but they did not enter
    assert m["reg_removed"] == []


def test_a_baseline_predating_the_signup_column_is_not_diffed(world, monkeypatch):
    """Blank means "unknowable", not "entered nothing". The schema rewrite
    blanks old rows, and reporting every entrant as newly added on the first
    run after it would be fabrication — which is why "-" exists."""
    world({"2026-07-26": {1: 0.40}}, live={1: 0.60}, schedule=GMC_SCHED,
          signed="",                                      # pre-schema row
          signed_live={1: {"reg_gmc": 1, "reg_mvp": 1}})

    m = _run(monkeypatch, dt.date(2026, 7, 27))["mpo"]["day"]["movers"][0]
    assert m["reg_added"] == []
