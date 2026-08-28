"""Resolving season outcomes for the backtest.

Two properties carry the module. First, an undecided outcome must be blank
rather than zero — evaluate skips a blank, but a zero scores every player as
having missed, which would silently invent a season's worth of outcomes.
Second, `capture` must only ever grow: a field readable today and dark
tomorrow has to survive, because that is the failure this exists to prevent.
"""
from __future__ import annotations

import csv
import json

from dgpt import actuals, config, live_api, schedule
from .conftest import finished_sheet, event_payload, round_payload, row


def _mark_completed() -> None:
    """Rewrite the tiny world's schedule with every event banked."""
    rows = schedule.load()
    for r in rows:
        r["completed"] = True
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        w.writerows(rows)


def _finish(fake_api, tid, name, players, rounds=3):
    """Give an event a complete, clean 1..N finish so it can bank."""
    places = list(range(1, len(players) + 1))
    fake_api.event(tid, event_payload(name, rounds, [("MPO", rounds)],
                                      end_date="2026-01-01"))
    for rnd in range(1, rounds + 1):
        fake_api.round(tid, "MPO", rnd, round_payload(finished_sheet(players, places)))


def _play_out_season(fake_api, world) -> None:
    """Bank every event the tiny world leaves unplayed.

    The playoffs finish in REVERSE order, so the final standings and the
    standings going into GMC are genuinely different tables — otherwise a test
    that reads the wrong one still passes.
    """
    _finish(fake_api, world.live_tid, "Test Live Open", world.players)
    _finish(fake_api, 900004, "Test Major", world.players)
    _finish(fake_api, config.TID_DOUBLES, "Test Doubles", world.players)
    _finish(fake_api, config.TID_GMC, "Test GMC", list(reversed(world.players)))
    _finish(fake_api, config.TID_MVP, "Test MVP", list(reversed(world.players)))
    # the Cup awards no points, so standings never fetches it
    live_api._memo.clear()
    _mark_completed()


def _repoint(monkeypatch, tmp_path):
    monkeypatch.setattr(actuals, "ACTUALS_DIR", tmp_path / "predictions")
    monkeypatch.setattr(actuals, "CAPTURE_FILE", tmp_path / "data" / "actual_fields.json")


# ------------------------------------------------------------------ blanks

def test_undecided_outcomes_are_blank_not_zero(tiny_world, monkeypatch, tmp_path):
    """The tiny world's playoffs and Cup are all in the future."""
    _repoint(monkeypatch, tmp_path)
    rows, notes = actuals.resolve("MPO")

    assert rows, "every standings player should get a row"
    for r in rows:
        assert r["auto_bid"] == "", "season is not over — auto_bid must be blank"
        assert r["made_gmc"] == "" and r["made_mvp"] == "" and r["made_cup"] == ""
        assert r["gmc_points_cut"] == "" and r["mvp_points_cut"] == ""
    assert any("pending" in n for n in notes)


def test_blank_outcomes_are_skipped_by_evaluate(tiny_world, monkeypatch, tmp_path):
    """A blank must not be read as 0.0 — that is the whole point of the shape."""
    from dgpt import evaluate

    _repoint(monkeypatch, tmp_path)
    hist = [{"pdga_number": "5001", "snapshot_date": "2026-08-01",
             "events_completed": "2", "p_cut": "0.9"}]
    assert evaluate._pair(hist[0], {5001: {"auto_bid": ""}}, "p_cut", "auto_bid") is None
    assert evaluate._pair(hist[0], {5001: {"auto_bid": "1"}}, "p_cut", "auto_bid") == (0.9, 1.0)
    # a prediction column that postdates the snapshot is equally unscoreable
    assert evaluate._pair(hist[0], {5001: {"auto_bid": "1"}}, "p_gmc_field", "auto_bid") is None


# ------------------------------------------------------------- auto bid

def test_auto_bid_resolves_once_the_season_is_banked(tiny_world, fake_api,
                                                    monkeypatch, tmp_path):
    _repoint(monkeypatch, tmp_path)
    _play_out_season(fake_api, tiny_world)
    rows, notes = actuals.resolve("MPO")
    by_pdga = {r["pdga_number"]: r for r in rows}

    cut = config.STANDINGS_CUT["MPO"]
    assert len([r for r in rows if r["auto_bid"] == 1]) == cut
    assert not any("auto_bid: pending" in n for n in notes)
    # every row is decided now — 0 is a real outcome, not a gap
    assert all(r["auto_bid"] in (0, 1) for r in rows)
    assert by_pdga[tiny_world.star]["final_rank"] == 1


def test_points_cut_uses_the_standings_that_set_the_field(tiny_world, fake_api,
                                                          monkeypatch, tmp_path):
    """The GMC cut is rank going INTO GMC, not off the final table.

    GMC and the MVP Open finish after the GMC field is chosen, so their points
    cannot count toward the cut that chose it. _play_out_season runs both in
    reverse order specifically so the two tables disagree — read the wrong one
    and this fails.
    """
    from dgpt import standings

    _repoint(monkeypatch, tmp_path)
    _play_out_season(fake_api, tiny_world)
    sched = schedule.load()
    table = standings.compute("MPO")
    asof = actuals._rank_asof(table, sched, "MPO", actuals._start_of(sched, config.TID_GMC))
    final = actuals._rank_asof(table, sched, "MPO", None)
    assert asof != final, "fixture is not exercising the distinction"

    rows, _ = actuals.resolve("MPO")
    cut = config.PLAYOFF_QUAL["gmc"]["cut"]["MPO"]
    for r in rows:
        assert r["gmc_points_cut"] == int(asof.get(r["pdga_number"], 10**6) <= cut)


# ------------------------------------------------------------------ capture

def test_capture_banks_a_readable_field(tiny_world, fake_api, monkeypatch, tmp_path):
    _repoint(monkeypatch, tmp_path)
    members = [p for p, _, _ in tiny_world.players[:6]]
    fake_api.round(config.TID_GMC, "MPO", 1,
                   round_payload([row(p, f"P{p}", 1000, played=0) for p in members]))
    # bring GMC inside the capture horizon
    monkeypatch.setattr(actuals, "_horizon", lambda days=45: "9999-99-99")

    notes = actuals.capture(divisions=("MPO",))
    assert notes and "6 in field" in notes[0]

    banked = json.loads(actuals.CAPTURE_FILE.read_text())
    assert banked[str(config.TID_GMC)]["MPO"] == sorted(members)


def test_capture_never_shrinks_when_a_source_goes_dark(tiny_world, fake_api,
                                                       monkeypatch, tmp_path):
    """The Pro Worlds failure mode: a staged roster vanishes mid-event.

    Once banked, the field must survive PDGA taking it away — otherwise the
    committed record silently degrades to whatever is readable in December.
    """
    _repoint(monkeypatch, tmp_path)
    monkeypatch.setattr(actuals, "_horizon", lambda days=45: "9999-99-99")
    members = [p for p, _, _ in tiny_world.players[:6]]
    fake_api.round(config.TID_GMC, "MPO", 1,
                   round_payload([row(p, f"P{p}", 1000, played=0) for p in members]))
    actuals.capture(divisions=("MPO",))

    # PDGA re-stages the event and the sheet comes back empty
    live_api._memo.clear()
    fake_api.round(config.TID_GMC, "MPO", 1, round_payload([]))
    actuals.capture(divisions=("MPO",))

    banked = json.loads(actuals.CAPTURE_FILE.read_text())
    assert banked[str(config.TID_GMC)]["MPO"] == sorted(members), "banked field was lost"
    assert actuals.field_members(config.TID_GMC, "MPO") == set(members)


def test_capture_unions_across_sources(tiny_world, fake_api, fake_pages,
                                       monkeypatch, tmp_path):
    """Roster and signup page each see a slice; the record is the union.

    The page is division-blind, so an entrant with no standings row in this
    division must not be admitted off it.
    """
    _repoint(monkeypatch, tmp_path)
    monkeypatch.setattr(actuals, "_horizon", lambda days=45: "9999-99-99")
    rostered = [p for p, _, _ in tiny_world.players[:4]]
    page_only = [p for p, _, _ in tiny_world.players[4:14]]
    stranger = 4242                       # no standings row in MPO

    fake_api.round(config.TID_GMC, "MPO", 1,
                   round_payload([row(p, f"P{p}", 1000, played=0) for p in rostered]))
    # needs >= live_api.MIN_PAGE_REGISTRANTS distinct players or the page
    # deliberately reads as "no list"
    fake_pages.signups(config.TID_GMC, page_only + [stranger])

    actuals.capture(divisions=("MPO",))
    got = actuals.field_members(config.TID_GMC, "MPO")
    assert got == set(rostered) | set(page_only)
    assert stranger not in got, "an out-of-division page entrant was admitted"


def test_unknown_field_is_none_not_empty(tiny_world, monkeypatch, tmp_path):
    """None ('cannot say') and set() ('nobody') must stay distinguishable."""
    _repoint(monkeypatch, tmp_path)
    assert actuals.field_members(config.TID_CHAMPIONSHIP, "MPO") is None


def test_capture_skips_events_beyond_the_horizon(tiny_world, monkeypatch, tmp_path):
    """No requests all season for a Cup that is months away."""
    _repoint(monkeypatch, tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(actuals, "_readable_field",
                        lambda tid, div, scope: calls.append(tid) or set())
    monkeypatch.setattr(actuals, "_horizon", lambda days=45: "1970-01-01")
    assert actuals.capture(divisions=("MPO",)) == []
    assert calls == []


# -------------------------------------------------------------------- file

def test_write_produces_a_file_evaluate_can_read(tiny_world, fake_api,
                                                 monkeypatch, tmp_path):
    _repoint(monkeypatch, tmp_path)
    _play_out_season(fake_api, tiny_world)
    msg = actuals.write("MPO")
    out = actuals.ACTUALS_DIR / "actuals_mpo.csv"
    assert out.exists() and "actuals_mpo.csv" in msg

    from dgpt import evaluate

    loaded = evaluate.load_actuals(out)
    assert loaded, "evaluate must be able to load what actuals writes"
    assert set(next(iter(loaded.values()))) == set(actuals.FIELDS)
    assert all(r["auto_bid"] in ("0", "1") for r in loaded.values())
