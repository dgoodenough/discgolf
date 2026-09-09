"""The forward-season projections: the 2027 DGPT tab and the EuroTour tab.

These have no results to check against and never will until the season is
played, so the tests here are about structure rather than accuracy: that the
announced schedule is transcribed faithfully, that the counting rules and
qualification ladder the projection claims to reuse are the ones it actually
applies, and that every published bundle is internally coherent.

The one number that CAN be checked is the schedule itself, and it is checked
hard — every date and designation comes from a press release someone typed in
by hand, and a transposed date silently changes an event's round count, which
changes its variance, which changes the odds.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from dgpt import config, fields, points, project, schedule, season2027

N_SIMS = 300


# ------------------------------------------------------------- the schedule

def test_schedule_matches_the_announcement():
    """Spot-checks against the 2027 announcement, including its edges."""
    by_short = {e.short_name: e for e in season2027.load()}
    assert len(season2027.load()) == 34

    opener = by_short["Greater Atlanta Open"]
    assert (opener.start_date, opener.end_date) == ("2027-02-26", "2027-02-28")
    assert opener.cls == "jomez" and opener.rounds == 3

    parnu = by_short["Pärnu Open"]
    assert parnu.cls == "et_a" and parnu.tour == "eurotour"

    worlds = by_short["Pro Worlds"]
    assert (worlds.start_date, worlds.end_date) == ("2027-06-16", "2027-06-20")
    assert worlds.rounds == 5, "the only five-day window on the calendar"

    cup = by_short["Powerball Cup"]
    assert cup.cls == "championship" and cup.rounds == season2027.CUP_ROUNDS
    assert cup.start_date > by_short["USDGC"].start_date, (
        "2027's postseason moves BEHIND the USDGC — that is the headline change"
    )


def test_division_only_majors():
    by_short = {e.short_name: e for e in season2027.load()}
    # the USDGC joins the MPO points race for the first time; it has no FPO field
    assert by_short["USDGC"].mpo and not by_short["USDGC"].fpo
    assert by_short["USWDGC"].fpo and not by_short["USWDGC"].mpo
    majors = [e for e in season2027.load() if e.cls == "major"]
    assert sum(e.mpo for e in majors) == 4 and sum(e.fpo for e in majors) == 4


def test_every_event_is_classified_and_dated():
    known_cls = set(config.MULTIPLIERS) | set(season2027.ET_MULTIPLIERS)
    seen_ids = set()
    for e in season2027.load():
        assert e.cls in known_cls, e.short_name
        assert e.tour in ("dgpt", "eurotour", "both"), e.short_name
        assert e.mpo or e.fpo, e.short_name
        assert 3 <= e.rounds <= 5, f"{e.short_name}: {e.rounds} rounds"
        assert e.event_id not in seen_ids, "event ids must be unique"
        seen_ids.add(e.event_id)
        dt.date.fromisoformat(e.start_date)
        assert e.end_date >= e.start_date


def test_playoff_ladder_is_ordered_and_last():
    """The two playoffs, then the Cup, and nothing after them."""
    dgpt = [e for e in season2027.load() if e.on("dgpt")]
    playoffs = [e for e in dgpt if e.cls == "playoff"]
    cup = next(e for e in dgpt if e.cls == "championship")
    assert [e.short_name for e in playoffs] == [
        season2027.DGPT.playoff1, season2027.DGPT.playoff2]
    assert playoffs[1].start_date > playoffs[0].end_date
    assert cup.start_date > playoffs[1].end_date
    assert max(e.end_date for e in dgpt) == cup.end_date


def test_eurotour_membership():
    et = [e for e in season2027.load() if e.on("eurotour")]
    assert len(et) == 8, "three A-Tiers, the Championships, two Elite stops, the EO, nationals"
    both = {e.short_name for e in et if e.tour == "both"}
    assert both == {"European Disc Golf Festival", "European Elite Series TBA", "European Open"}
    # an event on both tours is scored twice, at two different values
    eo_dgpt = season2027.curve("MPO", "major", "dgpt")
    eo_et = season2027.curve("MPO", "major", "eurotour")
    assert eo_dgpt[1] == eo_et[1] == 300.0


def test_dgpt_scoring_is_2026s_untouched():
    for cls in ("elite", "elite_plus", "major", "playoff", "doubles"):
        assert season2027.curve("MPO", cls, "dgpt") == points.event_curve("MPO", cls)


# --------------------------------------------------------------- fixtures

def _table(division: str, n: int, countries: dict[int, str], *, euro: int = 0) -> list[dict]:
    """A stand-in 2026 standings table: ratings, points and a start history.

    `euro` of the rows are given a European country so the EuroTour roster
    filter has something to select. Everyone plays every completed event,
    which pins participation rates at 1.0 and makes attendance deterministic —
    the projection's randomness is then only in the scores.
    """
    rows = []
    for i in range(n):
        pdga = 900_100 + i
        countries[pdga] = ["FI", "SE", "EE", "NO"][i % 4] if i < euro else "US"
        rows.append({
            "pdga_number": pdga, "name": f"Player {i:03d}", "rating": 1030 - i,
            "points": float(500 - i), "rank": i + 1, "starts": 2,
            "events": [(900001, 100.0, 1, "A"), (900002, 90.0, 2, "B")],
        })
    return rows


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A two-event 2026 season and a 40-player table, all local to the test."""
    countries: dict[int, str] = {}
    table = _table("MPO", 40, countries, euro=16)
    monkeypatch.setattr(fields, "load_countries", lambda: countries)
    monkeypatch.setattr(project, "DOCS_DATA", tmp_path / "data")
    sched = [
        {"tournament_id": 900001, "name": "A", "cls": "elite", "start_date": "2026-03-01",
         "end_date": "2026-03-03", "mpo": True, "fpo": True, "fpo_points": True, "completed": True},
        {"tournament_id": 900002, "name": "B", "cls": "elite", "start_date": "2026-04-01",
         "end_date": "2026-04-03", "mpo": True, "fpo": True, "fpo_points": True, "completed": True},
    ]
    return table, sched, countries, tmp_path / "data"


def _run(spec, world, division="MPO"):
    table, sched, _countries, _out = world
    return project.run(spec, division, table, sched, n_sims=N_SIMS, chunk=100)


# ------------------------------------------------------------- the DGPT tab

def test_dgpt_projection_is_coherent(world):
    res = _run(season2027.DGPT, world)
    n = len(res.names)
    assert n == 40, "everyone in the table with a rating is projected"

    for arr in (res.p_cut, res.p_champ, res.p_perf, res.p_play1, res.p_play2,
                res.p_cup_win, res.p_first):
        assert np.all(np.isfinite(arr)) and np.all((arr >= 0.0) & (arr <= 1.0))

    # one champion and one No. 1 per simulated season
    assert res.p_first.sum() == pytest.approx(1.0)
    assert res.p_cup_win.sum() == pytest.approx(1.0)
    # every automatic bid is in the Cup field, and everyone in it makes the Cup
    assert np.all(res.p_champ >= res.p_cut - 1e-12)
    assert np.all(res.p_cup_win <= res.p_champ + 1e-12)
    # the second playoff cannot be entered without qualifying for something
    assert np.all(res.p_play1 >= res.p_cut - 1e-12)
    # every row's finish distribution is one whole season
    assert np.allclose(res.rank_hist.sum(axis=1), res.n_sims)
    # ... and so is every row's Cup starting score, missing the field included
    assert np.allclose(res.strokes_hist.sum(axis=1), res.n_sims)


def test_nobody_starts_with_banked_points(world):
    """Last season's points are context, not a head start.

    The table is built so that rating order and 2026 points order agree, and
    then the check that matters is the negative one: a projected total is
    reachable from an empty ledger, never last season's total plus a season.
    """
    res = _run(season2027.DGPT, world)
    table, *_ = world
    for i, row in enumerate(table):
        assert res.mean_points[i] < 20_000
        assert res.mean_points[i] != pytest.approx(row["points"])
    assert res.mean_points[0] > res.mean_points[-1], "rating still orders the table"


def test_cup_field_respects_the_seed_ladder(world):
    res = _run(season2027.DGPT, world)
    values, _ = project.simulate._stroke_ladder("MPO")
    assert res.stroke_values == values
    # the best player tees off on a better score, on average, than the worst
    exp = (res.strokes_hist[:, :-1] * np.array(values)).sum(axis=1) / res.n_sims
    assert exp[0] < exp[-1]
    # the last bucket is "no tee time at all", and the top seed rarely lands in it
    assert res.strokes_hist[0, -1] < res.strokes_hist[-1, -1]


def test_playoff_gates_are_2026s(world):
    """The ladder is re-pointed at new venues, not redesigned."""
    post = project._Postseason.build(
        season2027.DGPT,
        [e for e in season2027.load() if e.on("dgpt") and e.plays("MPO")
         and e.cls != "championship"],
        "MPO",
    )
    assert post.cut1 == config.PLAYOFF_QUAL["gmc"]["cut"]["MPO"]
    assert post.fill1 == config.PLAYOFF_QUAL["gmc"]["fill"]["MPO"]
    assert post.cut2 == config.PLAYOFF_QUAL["mvp"]["cut"]["MPO"]
    assert post.perf2 == config.PLAYOFF_QUAL["mvp"]["perf"]["MPO"]
    assert post.standings_cut == 28 and post.perf_champ == 4
    assert post.ei1 < post.ei2, "the playoffs are drawn in calendar order"
    assert post.ei1 not in post.pre and post.ei2 not in post.pre


def test_counting_caps_bite(world):
    """Best-10 of a 14-event pool means four results are thrown away."""
    dgpt_events = [e for e in season2027.load()
                   if e.on("dgpt") and e.plays("MPO")
                   and e.cls in ("elite", "elite_plus", "doubles")]
    assert len(dgpt_events) == 14 > config.COUNT_DGPT

    c, n = 3, 2
    cols = {"dgpt": [np.full((c, n), float(v)) for v in range(1, 15)]}
    total = project._pool_total(cols, season2027.DGPT, c, n)
    # the best ten of 1..14 are 5..14
    assert np.all(total == sum(range(5, 15)))


def test_jomez_is_a_bonus_pool(world):
    """Seven JomezPro stops in 2027, and every one of them counts."""
    jomez = [e for e in season2027.load() if e.cls == "jomez"]
    assert len(jomez) == 7
    c, n = 2, 2
    cols = {"jomez": [np.full((c, n), 20.0) for _ in jomez]}
    total = project._pool_total(cols, season2027.DGPT, c, n)
    assert np.all(total == 140.0)


# ---------------------------------------------------------- the EuroTour tab

def test_eurotour_roster_is_european(world):
    res = _run(season2027.EUROTOUR, world)
    assert len(res.names) == 16
    assert all(cc in fields.EU_COUNTRIES for cc in res.countries)
    assert np.all(res.p_cup_win == 0.0), "there is no Cup on this tour"
    assert res.p_first.sum() == pytest.approx(1.0)


def test_eurotour_card_bands_partition_the_top(world):
    res = _run(season2027.EUROTOUR, world)
    cards = season2027.ET_CARDS["MPO"]
    assert np.all(res.p_full >= 0.0) and np.all(res.p_card >= -1e-12)
    # exactly one player per band per season
    assert res.p_full.sum() == pytest.approx(cards["full"], abs=1e-6)
    assert res.p_card.sum() == pytest.approx(cards["card"], abs=1e-6)
    # a Full Tour Card is strictly better than a EuroTour Card: the best player
    # takes more of the former and the bands never overlap
    assert res.p_full[0] > res.p_full[-1]
    assert np.all(res.p_full + res.p_card <= 1.0 + 1e-9)


def test_national_championships_need_a_field(world):
    """A country with one known player does not hand them a title.

    Small federations are pooled into a regional championship instead, so
    every player in the model has to beat somebody for the points.
    """
    countries = {1: "FI", 2: "FI", 3: "FI", 4: "FI", 5: "SE", 6: "NO", 7: "DK"}
    roster = [{"pdga_number": p} for p in range(1, 8)]
    groups = project._national_fields(roster, countries)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [3, 4], "Finland runs its own; SE/NO/DK are pooled"
    assert sum(len(g) for g in groups) == len(roster)


def test_a_lone_player_gets_no_free_title():
    roster = [{"pdga_number": 1}]
    assert project._national_fields(roster, {1: "IS"}) == []


# ------------------------------------------------------------------ export

@pytest.mark.parametrize("spec", [season2027.DGPT, season2027.EUROTOUR])
def test_export_bundle_is_well_formed(world, spec):
    _table, _sched, _countries, out_dir = world
    res = _run(spec, world)
    project.export(res)
    bundle = json.loads((out_dir / f"{spec.key}_mpo.json").read_text())
    meta, players, sched = bundle["meta"], bundle["players"], bundle["schedule"]

    assert meta["season"] == 2027 and meta["base_season"] == config.SEASON
    assert meta["notes"] == list(spec.notes), "the page shows the model's own list"
    assert meta["shown"] == len(players) <= meta["roster_size"]
    assert json.dumps(bundle) == json.dumps(bundle), "no NaN can reach the browser"
    assert "NaN" not in json.dumps(bundle)

    # `ei` indexes each player's attendance array; the Cup has no entry there
    n_att = len(players[0]["att"])
    assert [e["ei"] for e in sched if e["ei"] is not None] == list(range(n_att))
    assert all(e["ei"] is None for e in sched if e["cls"] == "championship")
    assert len(sched) == n_att + (1 if spec.championship else 0)

    for p in players:
        assert 0 <= p["mean_rank"] <= meta["roster_size"] + 1
        assert len(p["hist"]) == meta["max_hist_rank"]
        assert sum(p["hist"]) == pytest.approx(1.0, abs=1e-3)
        if spec.championship:
            assert len(p["strokes"]) == len(meta["start_strokes"]["values"]) + 1
        else:
            assert p["p_any"] == pytest.approx(p["p_full"] + p["p_card"], abs=1e-4)


def test_export_keeps_everyone_with_a_path_to_the_cup(world, monkeypatch):
    """The size cap never drops a player the forecast still gives a chance."""
    monkeypatch.setattr(project, "EXPORT_LIMIT", 3)
    _table, _sched, _countries, out_dir = world
    res = _run(season2027.DGPT, world)
    project.export(res)
    players = json.loads((out_dir / "2027_mpo.json").read_text())["players"]
    assert len(players) > 3
    kept = {p["pdga"] for p in players}
    for pdga, p_champ in zip(res.pdga_numbers, res.p_champ):
        if p_champ >= 0.0005:
            assert pdga in kept


def test_small_division_still_projects(monkeypatch, tmp_path):
    """A division holding fewer players than the qualification constants.

    The 2026 model had to be taught this (tests/test_small_division.py); the
    same shape reaches here through _top_k_by_place, whose k can exceed the
    table's own width.
    """
    countries: dict[int, str] = {}
    table = _table("FPO", 3, countries, euro=3)
    monkeypatch.setattr(fields, "load_countries", lambda: countries)
    monkeypatch.setattr(project, "DOCS_DATA", tmp_path / "data")
    sched = [{"tournament_id": 900001, "name": "A", "cls": "elite",
              "start_date": "2026-03-01", "end_date": "2026-03-03", "mpo": True,
              "fpo": True, "fpo_points": True, "completed": True}]
    for spec in (season2027.DGPT, season2027.EUROTOUR):
        res = project.run(spec, "FPO", table, sched, n_sims=100, chunk=50)
        assert len(res.names) == 3
        assert np.all(np.isfinite(res.mean_points))
        project.export(res)


def test_empty_roster_is_an_error_not_an_empty_page(monkeypatch, tmp_path):
    monkeypatch.setattr(fields, "load_countries", lambda: {})
    monkeypatch.setattr(project, "DOCS_DATA", tmp_path / "data")
    table = _table("MPO", 5, {}, euro=0)   # nobody European
    with pytest.raises(ValueError, match="nobody eligible"):
        project.run(season2027.EUROTOUR, "MPO", table, [], n_sims=10)


# ----------------------------------------------------- the refresh wiring

def test_refresh_writes_both_bundles(tiny_world):
    """The daily refresh's projection step, over the tiny world's standings."""
    from dgpt import refresh, standings

    table = standings.compute("MPO")
    refresh.project_season("MPO", table, schedule.load(), n_sims=100)
    out = project.DOCS_DATA
    assert (out / "2027_mpo.json").exists()
    # the tiny world is all-American, so the EuroTour has no roster and the
    # step declines to publish rather than inventing one
    assert not (out / "et_mpo.json").exists()


def test_a_broken_projection_never_fails_the_refresh(tiny_world, monkeypatch, capsys):
    """These tabs are experimental; the live 2026 forecast is the product."""
    from dgpt import refresh, standings

    def boom(*a, **k):
        raise RuntimeError("2027 schedule is nonsense")

    monkeypatch.setattr(project, "run", boom)
    refresh.project_season("MPO", standings.compute("MPO"), schedule.load(), n_sims=10)
    assert "projection skipped" in capsys.readouterr().out
