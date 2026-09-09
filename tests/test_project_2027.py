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
    known_cls = set(config.MULTIPLIERS) | {"et_a", "et_champs", "et_nat"}
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
        # a EuroTour event has a points category and nothing else does
        assert (e.et_cat is not None) == e.on("eurotour"), e.short_name
        assert e.et_cat in (None, 1, 2, 3), e.short_name


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
    # an event on both tours is scored twice, and at DIFFERENT values: the
    # European Open is a 300-point DGPT major and a 250-point EuroTour
    # category-1 event, which is the whole reason `curve` takes a tour
    eo = next(e for e in et if e.short_name == "European Open")
    assert season2027.curve("MPO", eo, "dgpt")[1] == 300.0
    assert season2027.curve("MPO", eo, "eurotour")[1] == 250.0


def test_dgpt_scoring_is_2026s_untouched():
    for ev in season2027.load():
        if ev.on("dgpt") and ev.cls in config.MULTIPLIERS:
            assert season2027.curve("MPO", ev, "dgpt") == points.event_curve("MPO", ev.cls)


@pytest.mark.parametrize("division", ["MPO", "FPO"])
def test_eurotour_points_match_the_published_table(division):
    """250 / 200 / 150 for a win, and 1,100 for a perfect season.

    The 1,100 is the arithmetic check on the whole structure: it only comes
    out if both the win values and the per-category counting caps are right.
    """
    wins = {}
    for ev in season2027.load():
        if ev.on("eurotour"):
            wins[ev.short_name] = season2027.curve(division, ev, "eurotour")[1]
    assert wins == {
        "European Championships": 250.0, "European Open": 250.0,
        "European Disc Golf Festival": 200.0, "European Elite Series TBA": 200.0,
        "Pärnu Open": 200.0,
        "RPM Open": 150.0, "Turku Open": 150.0, "National Championships": 150.0,
    }
    assert season2027.max_points() == 1100.0
    assert sum(p.keep for p in season2027.EUROTOUR.pools) == 6, "top 6 finishes"
    # category 3 lands exactly on the Elite Series curve, and 2 and 1 on the
    # DGPT's own DGPT+ and Playoff multipliers
    assert season2027.et_multiplier(division, 3) == pytest.approx(config.MULTIPLIERS["elite"])
    assert season2027.et_multiplier(division, 2) == pytest.approx(config.MULTIPLIERS["elite_plus"])
    assert season2027.et_multiplier(division, 1) == pytest.approx(config.MULTIPLIERS["playoff"])


def test_eurotour_pools_are_the_published_categories():
    """Category, not class: two A-Tiers sit in different pools."""
    by_short = {e.short_name: e for e in season2027.load()}
    pool = {n: project._pool_of(season2027.EUROTOUR, by_short[n])
            for n in ("RPM Open", "Turku Open", "Pärnu Open", "European Open",
                      "European Championships", "National Championships",
                      "European Disc Golf Festival", "European Elite Series TBA")}
    assert pool["Pärnu Open"] == "et2" != pool["Turku Open"] == "et3"
    assert pool["RPM Open"] == pool["National Championships"] == "et3"
    assert pool["European Open"] == pool["European Championships"] == "et1"
    assert pool["European Disc Golf Festival"] == "et2"
    counts = {p.name: sum(1 for v in pool.values() if v == p.name)
              for p in season2027.EUROTOUR.pools}
    assert counts == {"et1": 2, "et2": 3, "et3": 3}


def test_category_one_counts_only_the_better_of_two():
    """Winning both the European Championships and the European Open is worth
    no more than winning either — that is what "best 1 of 2" means, and it is
    the structure's sharpest edge."""
    c, n = 2, 2
    both = {"et1": [np.full((c, n), 250.0), np.full((c, n), 250.0)]}
    one = {"et1": [np.full((c, n), 250.0), np.zeros((c, n))]}
    assert np.all(project._pool_total(both, season2027.EUROTOUR, c, n) == 250.0)
    assert np.all(project._pool_total(one, season2027.EUROTOUR, c, n) == 250.0)


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
    """Both published bands are cumulative ranks, and the Full band nests
    inside the EuroTour one — so a player holds exactly one of the two."""
    res = _run(season2027.EUROTOUR, world)
    cards = season2027.ET_CARDS["MPO"]
    assert (cards["full"], cards["card_through"]) == (6, 24)
    assert season2027.ET_CARDS["FPO"] == {"full": 3, "card_through": 12}
    assert np.all(res.p_full >= 0.0) and np.all(res.p_card >= -1e-12)
    # every season deals exactly `full` Full cards and fills the band to
    # `card_through` — the EuroTour band is what is left of the wider one.
    # A table shorter than the band (this fixture holds 16 Europeans against a
    # 24-deep MPO band) simply hands everyone in it a card, which is the right
    # answer and not a case to special-case away.
    n = len(res.names)
    assert res.p_full.sum() == pytest.approx(min(cards["full"], n), abs=1e-6)
    assert res.p_card.sum() == pytest.approx(
        min(cards["card_through"], n) - cards["full"], abs=1e-6)
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


# ------------------------------------------------- the outside field

def test_only_the_open_events_carry_an_outside_field(world):
    """Three DGPT-calendar stops are open to the whole tour; the rest are not.

    The EuroTour's A-Tiers, its Championships and the national weekend have
    European fields, so the players missing from them are the domestic-only
    Europeans nothing in this repo can see. Inventing opponents there would
    paper over a stated blind spot rather than fix it.
    """
    table, sched, countries, _out = world
    roster = project._roster(season2027.EUROTOUR, table, countries)
    evs = [e for e in season2027.load()
           if e.on("eurotour") and e.plays("MPO") and e.cls != "championship"]
    rates = {r["pdga_number"]: {"us": 0.5, "eu": 0.5, "jomez": 0.5} for r in table}
    out = project._outside_fields(season2027.EUROTOUR, evs, roster, table, rates, countries)
    assert {evs[i].short_name for i in out} == {
        "European Disc Golf Festival", "European Elite Series TBA", "European Open"}
    # the pool is everyone in the standings without a row in this table
    n_out = len(table) - len(roster)
    assert all(rtg.size == n_out for rtg, _ in out.values())
    assert not any(countries[r["pdga_number"]] in fields.EU_COUNTRIES
                   for r in table if r["pdga_number"] not in
                   {q["pdga_number"] for q in roster})


def test_the_dgpt_tab_has_no_outside_field(world):
    """Its table already holds everyone who could enter."""
    table, _sched, countries, _out = world
    evs = [e for e in season2027.load() if e.on("dgpt") and e.plays("MPO")]
    roster = project._roster(season2027.DGPT, table, countries)
    assert project._outside_fields(season2027.DGPT, evs, roster, table, {}, countries) == {}


def test_the_outside_field_costs_the_table_points(world):
    """Racing 16 Europeans against each other at the European Open is not the
    same event as racing them against the tour, and the difference is exactly
    where categories 1 and 2 pay."""
    table, sched, _countries, _out = world
    with_outside = _run(season2027.EUROTOUR, world)

    import unittest.mock as mock
    with mock.patch.object(project, "_outside_fields", return_value={}):
        alone = _run(season2027.EUROTOUR, world)

    assert alone.mean_points.sum() > with_outside.mean_points.sum(), (
        "an open field can only push the table's finishes down"
    )
    # and the curve is deep enough to score a finish behind that field
    n = len(with_outside.names)
    assert np.all(with_outside.mean_points >= 0)
    assert n < len(table)


def test_curve_reaches_past_the_table(world):
    """A place earned behind an outside field must pay the curve, not zero."""
    table, sched, countries, _out = world
    roster = project._roster(season2027.EUROTOUR, table, countries)
    evs = [e for e in season2027.load()
           if e.on("eurotour") and e.plays("MPO") and e.cls != "championship"]
    rates = {r["pdga_number"]: {"us": 0.5, "eu": 0.5, "jomez": 0.5} for r in table}
    outside = project._outside_fields(season2027.EUROTOUR, evs, roster, table, rates, countries)
    drawer = project._Drawer(
        "MPO", evs, "eurotour", np.array([float(r["rating"]) for r in roster]),
        np.random.default_rng(1), None,
        [{"field_size": 0.0, "field_listed": 0.0, "field_avg_rating": 0.0} for _ in evs],
        outside,
    )
    assert drawer.depth == len(roster) + (len(table) - len(roster))
    eo = next(i for i, e in enumerate(evs) if e.short_name == "European Open")
    # last place in the combined field still pays the curve's floor
    assert drawer.curves[eo][drawer.depth] > 0.0
