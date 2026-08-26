"""Regression tests for live_api against captured payloads + synthetic shapes.

The captured payloads in tests/fixtures/payloads/ are the real API responses
behind this season's live-scoring incidents (see each test's docstring). When
the next shape variant bites, add the failing payload with
tests/fixtures/capture.py and start from a red test here — not from a one-shot
probe workflow.
"""
from __future__ import annotations

import pytest

from dgpt import live_api
from .conftest import event_payload, load_payload, round_payload, row

JOMEZ = 100195   # DGPT JomezPro Champions Landing Open 2026 (multi-layout)
USWDGC = 97341   # USWDGC 2026 (FPO major; finals sheet id 12, playoff sheet 13)


def serve_jomez(fake_api):
    fake_api.event(JOMEZ, load_payload(f"event_{JOMEZ}"))
    for rnd in (1, 2, 3):
        fake_api.round(JOMEZ, "MPO", rnd, load_payload(f"round_{JOMEZ}_MPO_{rnd}"))


def serve_uswdgc(fake_api):
    fake_api.event(USWDGC, load_payload(f"event_{USWDGC}"))
    for rnd in (1, 2, 3, 12, 13):
        fake_api.round(USWDGC, "FPO", rnd, load_payload(f"round_{USWDGC}_FPO_{rnd}"))


# ------------------------------------------------------------ Jomez (b4cfb56)

def test_jomez_live_field_sums_roundtopar(fake_api):
    """The multi-layout regression: per-sheet ToPar reads -16/-11/-10 for the
    winner because each sheet reports it against its own layout's par. Summing
    RoundtoPar reconstructs the true totals (model had Buhr 100% to finish 2nd
    in an event he won)."""
    serve_jomez(fake_api)
    field = live_api.live_field(JOMEZ, "MPO")
    assert field is not None
    buhr = next(v for v in field.values() if v["name"] == "Gannon Buhr")
    orum = next(v for v in field.values() if v["name"] == "Matthew Orum")
    assert buhr["cur"] == -10.0 and buhr["rem"] == 0.0
    assert orum["cur"] == -8.0
    assert min(v["cur"] for v in field.values()) == buhr["cur"]  # Buhr leads


def test_jomez_live_field_order_matches_running_place(fake_api):
    """Our reconstructed totals must never invert the sheet's own leaderboard:
    a strictly worse total may not hold a strictly better RunningPlace."""
    serve_jomez(fake_api)
    field = live_api.live_field(JOMEZ, "MPO")
    sheet = load_payload(f"round_{JOMEZ}_MPO_3")["scores"]
    place = {s["PDGANum"]: s["RunningPlace"] for s in sheet if s.get("RunningPlace")}
    rows = sorted(
        (rec["cur"], place[p]) for p, rec in field.items() if p in place
    )
    for (ca, pa), (cb, pb) in zip(rows, rows[1:]):
        if cb > ca + 1e-9:
            assert pb >= pa, f"total {cb:+g} placed {pb}, ahead of {ca:+g} at {pa}"


def test_jomez_final_results(fake_api):
    serve_jomez(fake_api)
    results = live_api.final_results(JOMEZ, "MPO", use_cache=False)
    assert results[0]["name"] == "Gannon Buhr" and results[0]["place"] == 1
    assert results[1]["name"] == "Matthew Orum" and results[1]["place"] == 2
    assert [r["place"] for r in results] == sorted(r["place"] for r in results)
    # DGPT points go to finishers only: every result posted every round
    r2_posted = {s["PDGANum"] for s in load_payload(f"round_{JOMEZ}_MPO_2")["scores"]
                 if s.get("HasRoundScore")}
    assert all(r["pdga_number"] in r2_posted for r in results)


def test_jomez_event_complete(fake_api):
    serve_jomez(fake_api)
    assert live_api.event_complete(JOMEZ, divisions=("MPO",)) is True


# --------------------------------------------------- USWDGC (6e99dd4/5094f16)

def test_uswdgc_final_results_survive_finals_round_ids(fake_api):
    """Finals live on sheet id 12 (no round 4, no round 11) and round 13 is a
    one-row sudden-death playoff sheet. final_results must ride the 404s and
    read the finishing order off the finals sheet."""
    serve_uswdgc(fake_api)
    results = live_api.final_results(USWDGC, "FPO", use_cache=False)
    assert len(results) == 77            # finals non-qualifiers still finished
    assert results[0]["place"] == 1
    assert [r["place"] for r in results] == sorted(r["place"] for r in results)


def test_uswdgc_live_field_keeps_non_finalists(fake_api):
    """Only 44 of 77 played the finals sheet; the other 33 must keep their
    three-round totals rather than vanish as withdrawals (the weather-delay
    failure mode: judging the field by the latest sheet alone)."""
    serve_uswdgc(fake_api)
    field = live_api.live_field(USWDGC, "FPO")
    assert field is not None and len(field) == 77
    sheets = [load_payload(f"round_{USWDGC}_FPO_{r}")["scores"] for r in (1, 2, 3)]
    non_finalist = next(
        s["PDGANum"] for s in load_payload(f"round_{USWDGC}_FPO_12")["scores"]
        if not s.get("HasRoundScore") and not (s.get("Played") or 0)
    )
    three_round_total = sum(
        float(next(x for x in sheet if x["PDGANum"] == non_finalist)["RoundtoPar"])
        for sheet in sheets
    )
    assert field[non_finalist]["cur"] == pytest.approx(three_round_total)


# ------------------------------------------------------- synthetic shapes

def _mini_event(fake_api, latest: int, sheets: dict[int, list[dict]], final_round: int = 3):
    fake_api.event(1, event_payload("Mini", final_round, [("MPO", latest)]))
    for rnd, rows in sheets.items():
        fake_api.round(1, "MPO", rnd, round_payload(rows))


def test_not_yet_started_players_stay_in_the_field(fake_api):
    """Round 1 loaded, nobody teed off: the field must not collapse to the
    few with scores (08d1e53) — everyone seeds from scratch at even par."""
    _mini_event(fake_api, 1, {1: [
        row(1, "A", 1030, played=0),
        row(2, "B", 1020, played=0),
        row(3, "C", 1010, round_to_par=-3, played=6),
    ]})
    field = live_api.live_field(1, "MPO")
    assert set(field) == {1, 2, 3}
    assert field[1]["cur"] == 0.0 and field[1]["rem"] == 3.0
    assert field[3]["cur"] == -3.0


def test_suspended_player_carries_prior_round(fake_api):
    """Weather suspension: a player mid-R1 while the sheet pointer moved to
    R2 keeps their partial score and remaining holes (USWDGC 2026 shape)."""
    _mini_event(fake_api, 2, {
        1: [row(1, "A", 1030, round_to_par=-4, played=18, has_score=True),
            row(2, "B", 1020, round_to_par=-2, played=13)],       # suspended mid-R1
        2: [row(1, "A", 1030, round_to_par=-1, played=9),
            row(2, "B", 1020, played=0)],                          # not started R2
    })
    field = live_api.live_field(1, "MPO")
    assert field[1]["cur"] == -5.0 and field[1]["rem"] == pytest.approx((54 - 27) / 18)
    assert field[2]["cur"] == -2.0 and field[2]["rem"] == pytest.approx((54 - 13) / 18)


def test_withdrawal_and_dns_leave_the_field(fake_api):
    _mini_event(fake_api, 2, {
        1: [row(1, "A", 1030, round_to_par=-4, played=18, has_score=True),
            row(2, "B", 1020, round_to_par=-2, played=18, has_score=True),
            row(3, "C", 1010, played=0)],                          # never started
        2: [row(1, "A", 1030, round_to_par=-1, played=9),
            row(2, "B", 1020, played=0, grand_total="999")],       # withdrew
    })
    field = live_api.live_field(1, "MPO")
    assert set(field) == {1}   # B withdrew; C is a mid-event DNS


def test_topar_fallback_when_roundtopar_missing(fake_api):
    """A sheet publishing no per-round score at all falls back to the sheet's
    running ToPar for the current round only."""
    _mini_event(fake_api, 2, {
        1: [row(1, "A", 1030, round_to_par=-4, played=18, has_score=True)],
        2: [row(1, "A", 1030, to_par=-6, played=9)],
    })
    field = live_api.live_field(1, "MPO")
    assert field[1]["cur"] == -6.0
    assert field[1]["rem"] == pytest.approx((54 - 27) / 18)


def test_missing_earlier_sheet_is_skipped(fake_api):
    """A restructured schedule can leave a hole in the round ids; only the
    latest sheet is allowed to hard-fail."""
    fake_api.event(1, event_payload("Mini", 3, [("MPO", 3)]))
    fake_api.round(1, "MPO", 1, round_payload(
        [row(1, "A", 1030, round_to_par=-4, played=18, has_score=True)]))
    fake_api.round(1, "MPO", 3, round_payload(
        [row(1, "A", 1030, round_to_par=-2, played=9)]))
    field = live_api.live_field(1, "MPO")
    assert field[1]["cur"] == -6.0


# ---------------------------------------------------- finals round ids (2026-07-30)

def _sheet(rows):
    return {"scores": rows}


def _row(pdga, name, rtp, played, done=1):
    return {"PDGANum": pdga, "Name": name, "Rating": 1000, "ToPar": None,
            "RoundtoPar": rtp, "Played": played, "HasRoundScore": done}


def test_finals_round_id_is_not_a_round_count(fake_api):
    """Ledgestone 2026 reports FinalRound=12 with RoundsList {1,2,3,12:Finals}.
    Reading 12 as a count told the model everyone had ~11 rounds left, which
    inflates the projected spread and flattens the live odds. The "Finals"
    round is a full fourth round for the whole field (verified: 156 MPO rows,
    18 holes, its own layout), so the count is 4 — not 3.""" 
    fake_api.event(1, event_payload("Ledgestone", 12, [("MPO", 1)], round_ids=[1, 2, 3, 12]))
    fake_api.round(1, "MPO", 1, _sheet([_row(10, "Heimburg", -11, 18)]))

    field = live_api.live_field(1, "MPO")
    assert field[10]["cur"] == -11.0
    assert field[10]["rem"] == 3.0  # 4 rounds, one played — not 11, and not 3


def test_only_listed_round_sheets_are_requested(fake_api):
    """The finals id must not make us request the nonexistent rounds 4-11."""
    fake_api.event(1, event_payload("Ledgestone", 12, [("MPO", 12)], round_ids=[1, 2, 3, 12]))
    for rnd, rtp in ((1, -5), (2, -3), (3, -2), (12, -4)):
        fake_api.round(1, "MPO", rnd, _sheet([_row(10, "A", rtp, 18)]))

    field = live_api.live_field(1, "MPO")
    requested = [c for c in fake_api.calls if "fetch_round" in c]
    assert len(requested) == 4                      # 1,2,3,12 — no 404 storm
    assert field[10]["cur"] == -14.0                # finals score included
    assert field[10]["rem"] == 0.0                  # past the regular rounds


# ------------------------------------------- unplayed final round (2026-08-09)

def test_event_with_final_round_still_to_play_is_not_complete(fake_api):
    """PDGA publishes the final round's sheet with tee times the night before.

    Reading "no score, no holes played" as "not in this round" declared the
    event over before anyone teed off: the Discmania Challenge was banked at
    2am on its final day with zero finishers, so it left the live view and
    never reached the standings."""
    fake_api.event(1, event_payload("Sunday To Come", 3, [("MPO", 3)]))
    for rnd in (1, 2):
        fake_api.round(1, "MPO", rnd, round_payload([
            row(10, "A", 1030, round_to_par=-5, played=18, has_score=True),
            row(11, "B", 1020, round_to_par=-3, played=18, has_score=True)]))
    fake_api.round(1, "MPO", 3, round_payload([          # sheet up, nobody out
        row(10, "A", 1030, played=0, tee_time="09:20:00", running_place=1),
        row(11, "B", 1020, played=0, tee_time="09:20:00", running_place=2)]))

    assert live_api.event_complete(1, ("MPO",)) is False


def test_finals_non_qualifier_does_not_block_completion(fake_api):
    """The other side of the same coin: a player with no tee time genuinely
    isn't in the round, and must not hold the event open forever."""
    fake_api.event(1, event_payload("Finals Field", 3, [("MPO", 3)]))
    for rnd in (1, 2):
        fake_api.round(1, "MPO", rnd, round_payload([
            row(10, "A", 1030, round_to_par=-5, played=18, has_score=True),
            row(11, "B", 1020, round_to_par=-3, played=18, has_score=True)]))
    fake_api.round(1, "MPO", 3, round_payload([
        row(10, "A", 1030, round_to_par=-2, played=18, has_score=True, running_place=1),
        row(11, "B", 1020, played=0, running_place=2)]))   # no tee time = not in it

    assert live_api.event_complete(1, ("MPO",)) is True


def test_unplayed_round_sheet_does_not_disqualify_the_field(fake_api):
    """The banking half: an unplayed sheet posts no scores, and intersecting
    it into the DNF filter emptied the finisher set, so final_results returned
    nobody and the event banked zero points."""
    fake_api.event(1, event_payload("Sheet Up Unplayed", 3, [("MPO", 3)]))
    for rnd in (1, 2):
        fake_api.round(1, "MPO", rnd, round_payload([
            row(10, "A", 1030, round_to_par=-5, played=18, has_score=True, running_place=1),
            row(11, "B", 1020, round_to_par=-3, played=18, has_score=True, running_place=2)]))
    fake_api.round(1, "MPO", 3, round_payload([
        row(10, "A", 1030, played=0, tee_time="09:20:00", running_place=1),
        row(11, "B", 1020, played=0, tee_time="09:20:00", running_place=2)]))

    results = live_api.final_results(1, "MPO", use_cache=False)
    assert [r["pdga_number"] for r in results] == [10, 11]


# ------------------------------------------------ playoff entries (2026-08-07)

def test_playoff_entry_is_not_a_round(fake_api):
    """A 3-round event with a sudden-death playoff listed is still 3 rounds.

    PDGA lists the playoff in RoundsList like any round, so counting the list
    gave every player in the field a phantom fourth round: pre-tee they read
    "rem 4.0" and, once the day tracker derived holes played from that, "thru
    0" halfway through round 1 (DGPT Discmania Challenge, 2026-08-07)."""
    fake_api.event(1, event_payload(
        "Three Rounds Plus Playoff", 3, [("MPO", 1)],
        round_ids=[1, 2, 3, 13], round_labels={1: "Round 1", 2: "Round 2",
                                               3: "Round 3", 13: "Playoff"}))
    fake_api.round(1, "MPO", 1, _sheet([_row(10, "A", -3, 9, done=0)]))

    field = live_api.live_field(1, "MPO")
    assert field[10]["rem"] == pytest.approx((54 - 9) / 18)   # not (72 - 9) / 18
    assert field[10]["thru"] == 9


def test_unlabelled_entry_past_final_round_is_a_playoff(fake_api):
    """No labels in the payload: FinalRound points at the last real round, so
    anything listed beyond it is the playoff."""
    fake_api.event(1, event_payload("No Labels", 3, [("MPO", 1)],
                                    round_ids=[1, 2, 3, 13]))
    fake_api.round(1, "MPO", 1, _sheet([_row(10, "A", -3, 18)]))
    assert live_api.live_field(1, "MPO")[10]["rem"] == 2.0


def test_uswdgc_playoff_sheet_is_neither_counted_nor_read(fake_api):
    """The captured shape: RoundsList {1, 2, 3, 12: Finals, 13: Playoff}. The
    finals are a real fourth round; the playoff is one row over 8 holes that
    PDGA leaves out of everyone's ToPar. Counting it left the whole field a
    round short of finished after the event was over."""
    serve_uswdgc(fake_api)
    field = live_api.live_field(USWDGC, "FPO")
    winner = next(v for v in field.values() if v["name"] == "Silva Saarinen")
    assert winner["rem"] == 0.0      # 4 rounds played, none left — not 1.0
    assert winner["thru"] == 72
    assert not any("Round=13" in c for c in fake_api.calls)


# ------------------------------------------------- holes played (2026-08-07)

def test_thru_counts_holes_played_not_rounds_remaining(fake_api):
    """Holes played rides along explicitly so no consumer re-derives it.

    Deriving it as (rounds - rem) * 18 silently depends on the event's real
    round count matching the model's per-class constant — 3, or 4 for a major.
    Any event where those differ (here: four rounds, non-major class) makes the
    derivation land a whole round out, and the callers read thru <= 0 as "yet
    to tee off", so the score disappears along with the hole count."""
    fake_api.event(1, event_payload("Four Rounds", 4, [("MPO", 1)]))
    fake_api.round(1, "MPO", 1, _sheet([_row(10, "A", -3, 9, done=0),
                                        _row(11, "B", -1, 18)]))

    field = live_api.live_field(1, "MPO")
    assert field[10]["thru"] == 9
    assert field[11]["thru"] == 18
    assert round((3 - field[10]["rem"]) * 18) == -9   # the old derivation


def test_thru_is_zero_before_teeing_off(fake_api):
    _mini_event(fake_api, 1, {1: [row(1, "A", 1030, played=0)]})
    assert live_api.live_field(1, "MPO")[1]["thru"] == 0


def test_thru_accumulates_across_round_sheets(fake_api):
    """Suspended-player shape: a completed round plus a partial one."""
    _mini_event(fake_api, 2, {
        1: [row(1, "A", 1030, round_to_par=-4, played=18, has_score=True)],
        2: [row(1, "A", 1030, round_to_par=-1, played=9)],
    })
    assert live_api.live_field(1, "MPO")[1]["thru"] == 27


def test_plain_and_major_round_counts_unchanged(fake_api):
    """Regression: events whose FinalRound really is a count still work."""
    fake_api.event(1, event_payload("Jomez", 3, [("MPO", 1)]))
    fake_api.round(1, "MPO", 1, _sheet([_row(10, "A", -6, 18)]))
    assert live_api.live_field(1, "MPO")[10]["rem"] == 2.0

    fake_api.event(2, event_payload("USWDGC", 4, [("FPO", 1)]))
    fake_api.round(2, "FPO", 1, _sheet([_row(20, "B", -5, 12, done=0)]))
    assert live_api.live_field(2, "FPO")[20]["rem"] == pytest.approx((72 - 12) / 18)


# --------------------------------------- empty payloads arrive as JSON lists

def test_empty_round_sheet_arrives_as_a_list(fake_api):
    """Pro Worlds 2026 (2026-08-26): the event was staged for live scoring a
    day before round 1, so PDGA served `"data": []` for the round-1 sheet — an
    empty PHP array, not the usual object. Every `.get("scores")` raised
    AttributeError, livecheck crashed on all three loop iterations, the run
    failed, and the site stopped updating with Worlds about to tee off.

    An empty sheet must read as "no scores yet", which is the answer live_field
    already has a fallback for (None -> simulate the event from scratch)."""
    fake_api.event(1, event_payload("Worlds", 4, [("MPO", 1)]))
    fake_api.envelopes[
        f"{live_api.BASE}/live_results_fetch_round?TournID=1&Division=MPO&Round=1"
    ] = {"data": []}

    assert live_api.fetch_round(1, "MPO", 1) == {}
    assert live_api.live_field(1, "MPO") is None
    assert live_api.event_complete(1, ("MPO",)) is False
    assert live_api.registration_list(1, "MPO") is None


def test_empty_event_payload_arrives_as_a_list(fake_api):
    """Same shape on the event endpoint: no divisions, so nothing is live."""
    fake_api.envelopes[
        f"{live_api.BASE}/live_results_fetch_event?TournID=1"
    ] = {"data": []}

    assert live_api.fetch_event(1) == {}
    assert live_api.live_field(1, "MPO") is None
    assert live_api.event_complete(1, ("MPO",)) is False
    assert live_api.final_results(1, "MPO") == []


def test_a_bare_list_of_rows_is_read_as_the_sheet(fake_api):
    """The defence must not be able to empty a sheet that does carry scores:
    a payload that drops the {"scores": ...} wrapper keeps its rows."""
    fake_api.event(1, event_payload("Mini", 3, [("MPO", 1)]))
    rows = [row(1, "A", 1030, round_to_par=-4, played=18, has_score=True)]
    fake_api.envelopes[
        f"{live_api.BASE}/live_results_fetch_round?TournID=1&Division=MPO&Round=1"
    ] = {"data": rows}

    field = live_api.live_field(1, "MPO")
    assert field is not None and field[1]["cur"] == -4.0
