"""Results fetcher using PDGA's public live-scoring API (no auth required).

live_results_fetch_event gives divisions + final round number; the final
round's scores carry RunningPlace = finishing place. Requests are throttled
and completed-event responses are cached to disk since they never change.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from . import config

BASE = "https://www.pdga.com/apps/tournament/live-api"
UA = {"User-Agent": "dgpt-forecast/1.0 (github.com/dgoodenough/discgolf)"}
LIVE_CACHE = config.CACHE_DIR / "live"
RESULTS_CACHE = config.CACHE_DIR / "results"

# Flight recorder: a rolling archive of raw API responses in the results cache
# (persisted across CI runs by actions/cache, gitignored locally). Every
# one-shot probe workflow this season existed because the payload that caused
# a wrong number was gone by the time it was investigated — this keeps the
# recent history of each sheet so a bad number can be replayed locally
# (tests/fixtures/capture.py is the tool for promoting one to a fixture).
# Snapshots are written only when a sheet's content actually changed; the
# newest FLIGHT_KEEP versions per sheet are retained.
FLIGHT_DIR = config.CACHE_DIR / "flight"
FLIGHT_KEEP = 48


def _flight_name(url: str) -> str | None:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    tid = (q.get("TournID") or ["x"])[0]
    if "live_results_fetch_round" in url:
        return f"round_{tid}_{(q.get('Division') or ['x'])[0]}_{(q.get('Round') or ['x'])[0]}"
    if "live_results_fetch_event" in url:
        return f"event_{tid}"
    return None


def _record_flight(url: str, data: dict) -> None:
    """Archive one raw response. Must never break a fetch: best-effort only."""
    name = _flight_name(url)
    if name is None or os.environ.get("DGPT_FLIGHT_OFF"):
        return
    try:
        payload = json.dumps(data, separators=(",", ":"))
        FLIGHT_DIR.mkdir(parents=True, exist_ok=True)
        snaps = sorted(FLIGHT_DIR.glob(f"{name}.*.json"))
        if snaps and snaps[-1].read_text(encoding="utf-8") == payload:
            return  # unchanged since the newest snapshot
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        (FLIGHT_DIR / f"{name}.{stamp}.json").write_text(payload, encoding="utf-8")
        for old in snaps[: max(len(snaps) + 1 - FLIGHT_KEEP, 0)]:
            old.unlink()
    except OSError:
        pass

_MIN_INTERVAL = 0.5  # be polite: max ~2 req/s
_last_request = 0.0
# per-process memo: registration/live lookups hit the same round-1 URLs from
# several places in one refresh (fields, roster, doubles) — fetch each once.
# A refresh is a fresh process, so this never serves stale data across runs.
_memo: dict[str, dict] = {}


def _get(url: str, cache_file: Path | None = None) -> dict:
    global _last_request
    if cache_file and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    if url in _memo:
        return _memo[url]
    for backoff in (0, 5, 15, 45):
        if backoff:
            time.sleep(backoff)
        wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                continue
            raise
    else:
        raise RuntimeError(f"still rate-limited after retries: {url}")
    _record_flight(url, data)
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    _memo[url] = data
    return data


def _body(envelope: dict) -> dict:
    """The `data` body of a live-API response, normalized to an object.

    PDGA serializes an EMPTY payload as a JSON list — `"data": []` — because
    the PHP array behind it has no keys to make it an object. Pro Worlds 2026
    was staged for live scoring with its round sheets still empty, so the
    moment its start date opened the live window every caller doing
    `.get("scores")` died with `'list' object has no attribute 'get'`. That is
    livecheck, which crashed on all three loop iterations, failed the run, and
    left the site serving Sunday's numbers a day before round 1 (2026-08-26).
    Sixth live-API shape variant this season; see HARDENING.md item 1.

    A NON-empty list is read as the rows themselves rather than discarded, so
    this can never quietly empty a sheet that really does carry scores.
    """
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data:
        if len(data) == 1 and isinstance(data[0], dict) and "scores" in data[0]:
            return data[0]
        return {"scores": data}
    return {}


def fetch_event(tournament_id: int, *, cache: bool = False) -> dict:
    cf = LIVE_CACHE / f"event_{tournament_id}.json" if cache else None
    return _body(_get(f"{BASE}/live_results_fetch_event?TournID={tournament_id}", cf))


def fetch_round(tournament_id: int, division: str, round_num: int, *, cache: bool = False) -> dict:
    cf = LIVE_CACHE / f"round_{tournament_id}_{division}_{round_num}.json" if cache else None
    url = f"{BASE}/live_results_fetch_round?TournID={tournament_id}&Division={division}&Round={round_num}"
    return _body(_get(url, cf))


def _day_span(event: dict) -> int | None:
    """How many days the event runs, or None if the payload doesn't say.

    An upper bound on rounds played: no 2026 DGPT event plays two rounds in a
    day, so a 3-day event plays at most 3.
    """
    start, end = event.get("StartDate"), event.get("EndDate")
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    except ValueError:
        return None


def _real_rounds(event: dict, latest: int | None = None) -> list[int]:
    """The ids of rounds the field actually plays, in order (no playoff).

    `latest` bounds the list to sheets that exist; omit it for the full plan.
    """
    if latest is None:
        listed = [int(k) for k in (event.get("RoundsList") or {}) if str(k).isdigit()]
        # not an arbitrary large number: _round_plan's no-RoundsList fallback
        # builds range(1, latest + 1), so an unbounded value allocates it
        latest = max(listed) if listed else int(event.get("FinalRound") or event.get("Rounds") or 1)
    ids, n = _round_plan(event, int(latest))
    return ids[:n]


def _is_playoff(entry: dict, rid: int, final: int | None) -> bool:
    """Is this RoundsList entry a sudden-death playoff rather than a round?

    Prefers PDGA's own label ("Playoff" / "P", vs "Round 3" or "Finals").
    Where an entry carries no label at all, falls back to the position of
    FinalRound, which points at the last real round — anything listed beyond
    it is a playoff.
    """
    label = " ".join(str(entry.get(k) or "") for k in ("Label", "LabelAbbreviated")).strip()
    if label:
        return "playoff" in label.lower()
    return final is not None and rid > final


def _round_plan(event: dict, latest: int) -> tuple[list[int], int]:
    """(sheet ids to read, number of rounds the field plays).

    "FinalRound" is NOT a round count — where an event has a round PDGA labels
    "Finals", it is that round's ID. Ledgestone 2026 reports FinalRound=12 with
    RoundsList {1, 2, 3, 12: "Finals"}. Treating 12 as a count told the
    remaining-holes model everyone had ~11 rounds left, which inflates the
    projected spread enormously and flattens the live win odds (caught by the
    rem bounds invariant, 2026-07-30).

    Nor is the "Finals" round a top-card shootout to be excluded: at Ledgestone
    its sheet carries the full field (156 MPO / 55 FPO rows) over 18 holes on
    its own layout — it is simply the fourth round, numbered oddly. Ledgestone
    is a four-round event. "Rounds" is no good either, since it reports 3 there
    (numbered rounds only).

    But a sudden-death PLAYOFF is listed in RoundsList too, and it is not a
    round: at USWDGC 2026 id 13 ("Playoff") is a one-row sheet over 8 holes,
    and PDGA does not fold its strokes into anyone's ToPar. Counting every
    listed id therefore handed the whole field a phantom round — a 3-round
    event with a playoff listed read "rem 4.0" for everyone before a disc was
    thrown (DGPT Discmania Challenge, 2026-08-07), inflating the projected
    spread and flattening the live win odds exactly the way the FinalRound bug
    above did. It slips past the rem bounds invariant because 4 rounds is a
    perfectly legal number.

    So: every listed round counts, except a playoff. PDGA labels it, and
    "FinalRound" independently points at the last real round (12 at USWDGC,
    with the playoff at 13) — we use the label where there is one and the
    FinalRound bound for payloads that carry no labels.

    Backstop for both: a tournament cannot play more rounds than it has days.
    The date span is the one number PDGA cannot get creative with, and it
    matches every 2026 event (3-day elites play 3, 4-day majors and DGPT+
    play 4; Worlds runs 4 rounds over 5 days, so this only ever caps). When
    the span says fewer rounds than the list does, the extra ids are not
    rounds whatever they are labelled, and the last ones listed go first.

    Caveat for a future variant: a genuine 9-hole Final 9 would be counted as a
    whole round here, overstating rem by half a round for the players in it.
    No 2026 event does that — every listed round has been a full 18 — and the
    hole counts needed to do better only exist on sheets already published.

    Iterating the listed ids rather than 1..latest also stops us requesting the
    nonexistent rounds 4-11 on every refresh, and keeps the playoff sheet out
    of the score accumulation entirely.
    """
    rounds_list = event.get("RoundsList") or {}
    ids = sorted(int(k) for k in rounds_list if str(k).isdigit())
    if not ids:  # older/degraded payloads: fall back to a contiguous range
        n = event.get("Rounds") or latest
        ids = list(range(1, max(int(n), int(latest)) + 1))
    else:
        final = event.get("FinalRound")
        final = int(final) if final else None
        ids = [
            i for i in ids
            if not _is_playoff(rounds_list.get(str(i)) or rounds_list.get(i) or {}, i, final)
        ]
    span = _day_span(event)
    if span and len(ids) > span:
        ids = ids[:span]
    return [i for i in ids if i <= latest], len(ids)


def event_complete(tournament_id: int, divisions: tuple[str, ...] = ("MPO", "FPO")) -> bool:
    """True once every (non-withdrawn) player in each relevant division has a
    final-round score — so the event can be banked into the standings the
    moment it finishes rather than waiting for the date to pass. Conservative:
    if it can't confirm, returns False and the date-based fallback applies.

    The event-level "HighestCompletedRound" is unreliable here (it advances
    when the fastest division finishes, while another may still be on course),
    so we check each division's final round directly.

    "Final round" means the last round the field plays, from the round plan —
    NOT raw FinalRound, which can point at a sudden-death playoff. Reading a
    playoff sheet here confirms completion off one or two rows.
    """
    event = fetch_event(tournament_id)
    rounds = _real_rounds(event)
    final = rounds[-1] if rounds else None
    if not final:
        return False
    present = {d["Division"] for d in (event.get("Divisions") or [])}
    # Skipping a division PDGA doesn't carry is right for USWDGC (no MPO), but
    # if NONE of them are there we have confirmed nothing at all and the loop
    # below would fall through to "complete" — banking an event off an empty
    # payload, which is how the Discmania Challenge banked zero finishers.
    if not present & set(divisions):
        return False
    for div in divisions:
        if div not in present:
            continue
        d = next(x for x in (event.get("Divisions") or []) if x["Division"] == div)
        if (d.get("LatestRound") or 0) < final:
            return False  # not on the final round yet
        scores = fetch_round(tournament_id, div, final).get("scores") or []
        if not scores:
            return False
        for s in scores:
            if s.get("HasRoundScore") or str(s.get("GrandTotal")) == "999":
                continue  # finished, or withdrawn
            if (s.get("Played") or 0) > 0:
                return False  # mid-round — still on the course
            # No score and no holes played is two different situations, and
            # they were being conflated: a player who is not in this round
            # (a finals non-qualifier — USWDGC's finals sheet lists 33 of them
            # with TeeTime "" and HasGroupAssignment 0), or a player whose
            # round simply has not started yet. PDGA publishes the final
            # round's sheet with tee times assigned the night before, so
            # treating both as "ignore" declared the event finished hours
            # before anyone teed off — which banked the Discmania Challenge
            # with zero finishers and made it vanish (2026-08-09).
            #
            # An upcoming tee time means the tournament is not done.
            if s.get("TeeTime") or s.get("HasGroupAssignment"):
                return False
    return True


def doubles_teams(tournament_id: int, division: str) -> dict[int, dict]:
    """Team pairings for the doubles championship: {pdga: {partner, partner_name}}.

    Prefers PDGA Live's team fields (authoritative once the event is staged
    for live scoring); until those populate, parses the Disc Golf Scene
    registration page, which lists teams as they register. Both sources are
    fetched fresh on every refresh, so new teams appear automatically.
    Players registered without a listed partner are omitted (the sim pairs
    them with a field-average partner).
    """
    import re

    out: dict[int, dict] = {}

    # 1) PDGA Live (empty until event week, then authoritative)
    try:
        scores = fetch_round(tournament_id, division, 1).get("scores") or []
        for s in scores:
            mates = s.get("Teammates") or []
            me = s.get("PDGANum")
            for m in mates:
                mp = m.get("PDGANum") if isinstance(m, dict) else None
                if me and mp and mp != me:
                    out[me] = {"partner": mp, "partner_name": m.get("Name")}
        if out:
            return out
    except Exception:
        pass

    # 2) DGS registration page fallback
    try:
        req = urllib.request.Request(config.DOUBLES_REG_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:
        return out
    i = raw.find(f'id="tournament-registration-players-{division}"')
    if i < 0:
        return out
    j = raw.find('id="tournament-registration-players-', i + 10)
    seg = raw[i: j if j > 0 else len(raw)]

    team: list[tuple[int, str]] = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", seg, re.S):
        if 'class="team-name"' in row:  # first member row starts a team
            team = []
        m = re.search(r'profile/\d+">([^<]+)</a>.*?pdga\.com/player/(\d+)', row, re.S)
        if not m:
            m = re.search(r"<td>([^<]+?)\s*</td>\s*<td><a[^>]*pdga\.com/player/(\d+)", row, re.S)
        if m:
            team.append((int(m.group(2)), m.group(1).strip()))
        if len(team) == 2:
            (a, an), (b, bn) = team
            out[a] = {"partner": b, "partner_name": bn}
            out[b] = {"partner": a, "partner_name": an}
            team = []
    return out


def registered_roster(tournament_id: int, division: str) -> dict[int, dict]:
    """Name + rating for everyone on an event's registration list (PDGA Live
    preloads rosters well before play). Used to give first-start players a
    row before their debut event.

    Falls back to the public event page when live scoring carries no roster,
    the same two-source order registration_list already uses for the playoffs.
    Pro Worlds needed it: the round-1 sheet held 208 MPO / 92 FPO entrants for
    a week, then went empty when the TD re-staged the event for live scoring
    (2026-08-26). Reading that as "no field" put the whole roster back on
    participation rates on the morning of the event — a 114-player smear where
    nobody was excluded and nobody was certain — and dropped the 71 entrants
    with no standings row off the site entirely.

    Page entries carry no name or rating, and no division: callers already
    scope by division (play_probabilities iterates a division roster,
    _build_roster requires a division rating), so an entrant from the other
    division simply never matches.
    """
    return (_live_roster(tournament_id, division)
            or {p: {"name": None, "rating": None}
                for p in page_registrants(tournament_id)})


def _live_roster(tournament_id: int, division: str) -> dict[int, dict]:
    """PDGA Live's own roster: empty until a TD stages the event, and empty
    again if they re-stage it. Kept separate from registered_roster because
    registration_list reads THIS one — a page list is explicitly not the final
    field ("real but still growing"), and letting the fallback reach
    registration_list would return one as staged=True and close a playoff
    field the moment its waves opened.
    """
    try:
        scores = fetch_round(tournament_id, division, 1).get("scores") or []
    except (urllib.error.HTTPError, KeyError):
        return {}
    return {
        s["PDGANum"]: {"name": s.get("Name"), "rating": s.get("Rating")}
        for s in scores
        if s.get("PDGANum")
    }


# --------------------------------------------------- public signup lists

EVENT_PAGE = "https://www.pdga.com/tour/event/{tid}"
# A page with a signup list says so; without this marker a parse would be
# reading some other page (a 404 shell, a redirect, a markup rewrite).
PAGE_MARKERS = ("Registered Players", "registered-players", "Registration is")
MIN_PAGE_REGISTRANTS = 8
_page_memo: dict[int, frozenset[int]] = {}


def _fetch_page(url: str) -> str:
    """Seam for the one non-API fetch: PDGA's public event page."""
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def page_registrants(tournament_id: int) -> frozenset[int]:
    """PDGA numbers linked from an event page's public signup list.

    Division-blind on purpose. The page lists every division at once, and
    every caller intersects the result with a division roster, which is
    already division-exclusive — an FPO entrant simply has no MPO row to
    match against. Splitting the page by division would mean guessing at
    per-division markup for no gain.

    Guarded twice, because a silent mis-parse here would invent a field: the
    page must carry a registration marker, and must yield at least
    MIN_PAGE_REGISTRANTS players. A page that links only its TD, or whose
    markup moved, reads as "no list" and the caller falls back.

    This is the source for the qualification-gated events (the playoffs and
    the Worlds play-in), whose signups are public months before a TD stages
    them for live scoring — which is the only thing PDGA Live knows about.
    """
    import re

    if tournament_id in _page_memo:
        return _page_memo[tournament_id]
    found: frozenset[int] = frozenset()
    try:
        raw = _fetch_page(EVENT_PAGE.format(tid=tournament_id))
        if any(m in raw for m in PAGE_MARKERS):
            nums = {int(m) for m in re.findall(r'href="[^"]*/player/(\d+)', raw)}
            if len(nums) >= MIN_PAGE_REGISTRANTS:
                found = frozenset(nums)
    except Exception:  # noqa: BLE001 - a signup list is never worth a failed refresh
        pass
    _page_memo[tournament_id] = found
    return found


def registration_list(tournament_id: int, division: str) -> tuple[dict[int, dict], bool] | None:
    """Everyone signed up for an event, and whether that list is the final field.

    Returns ({pdga_number: {name, rating}}, staged). PDGA Live first —
    structured, cached, division-scoped, and what every other field read in
    the pipeline uses; a TD only stages an event there once the field is set,
    so staged=True means "this IS the field", the same trust the rest of the
    pipeline already places in it.

    Until then, a qualification-gated event (the playoffs, the Worlds play-in)
    publishes its signups on the PDGA event page months ahead. That list is
    real but still growing, so staged=False: callers keep whatever other route
    into the field they were modelling. Page entries carry no name or rating —
    and no division, so callers must scope them themselves.

    None means neither source published a list, which is distinct from an
    empty one: it is the signal to keep the pre-signup assumption entirely.
    """
    roster = _live_roster(tournament_id, division)
    if roster:
        return roster, True
    nums = page_registrants(tournament_id)
    return ({p: {"name": None, "rating": None} for p in nums}, False) if nums else None


def live_field(tournament_id: int, division: str) -> dict[int, dict] | None:
    """Current standing of an in-progress event, for the remaining-holes model.

    Returns {pdga_number: {name, rating, cur (to-par), rem (rounds left)}} for
    every player in the field (excluding withdrawals), or None if the round
    isn't loaded yet (fall back to the from-scratch simulation).

    Registered players who have not teed off in round 1 yet carry a null ToPar
    in PDGA Live but are still in the field — they must be seeded from scratch
    (even par, all rounds remaining). Dropping them would collapse an early-
    morning field to the handful already on the course and hand those few the
    whole win-probability mass. The DGPT has no cut in regular rounds, so a
    null total in a later round means not-in-round (withdrawn) rather than
    not-started, and we leave those out.
    """
    event = fetch_event(tournament_id)
    div = next((d for d in (event.get("Divisions") or []) if d["Division"] == division), None)
    if div is None:
        return None
    latest = div.get("LatestRound")
    if not latest:
        return None
    sheet_ids, total_rounds = _round_plan(event, latest)
    if not total_rounds:
        return None

    # Accumulate each player's state across ALL round sheets 1..latest rather
    # than reading the latest sheet alone. Weather suspensions leave players
    # mid-round or a full round behind while the event's sheet pointer
    # advances (USWDGC 2026: 38 players suspended mid-R1 — the co-leader
    # among them — while LatestRound moved to 2); judging by the latest sheet
    # dropped them all as withdrawn. A player leaves the field only on the
    # explicit withdrawal marker (GrandTotal 999) or by never having played
    # once the event is past its first round.
    #
    # Score source: SUM the per-round "RoundtoPar" across sheets. The
    # round-total "ToPar" cannot be summed and is not comparable between
    # sheets — at multi-layout events each round's sheet reports it against
    # that round's par, so the same player reads differently on each sheet
    # (Jomez Champions Landing 2026: Buhr's sheets said -16 / -11 / -10 while
    # his actual rounds were -6 / -1 / -3). Preferring ToPar therefore latched
    # a value from the wrong layout and inverted the leaderboard — the model
    # had the runner-up winning. RoundtoPar is per-round and layout-local, so
    # summing it reconstructs the true total exactly (Buhr -10 = 1st,
    # Orum -8 = 2nd, matching the field's RunningPlace), and it is live
    # mid-round (it counts only holes played so far).
    #
    # ToPar is used only as a fallback for a round that reports no
    # RoundtoPar at all, and only for the current round, where it is the
    # running total on the sheet being played.
    state: dict[int, dict] = {}
    for rnd in sheet_ids:
        try:
            scores = fetch_round(tournament_id, division, rnd).get("scores") or []
        except urllib.error.HTTPError:
            if rnd == latest:
                raise
            continue  # earlier sheet missing (restructured schedule) — skip it
        for s in scores:
            pdga = s.get("PDGANum")
            if not pdga:
                continue
            rec = state.setdefault(pdga, {"name": None, "rating": None, "cur": None,
                                          "holes": 0, "wd": False, "place": None})
            rec["name"] = s.get("Name") or rec["name"]
            rec["rating"] = s.get("Rating") or rec["rating"]
            if str(s.get("GrandTotal")) == "999":
                rec["wd"] = True
            # the latest sheet carrying a RunningPlace is the current standing.
            # It is the sheet's own authoritative ordering (the same field the
            # invariants check against), so it beats re-deriving a place by
            # sorting on cur — which would tie players who are mid-round with
            # different holes played.
            if s.get("RunningPlace"):
                rec["place"] = int(s["RunningPlace"])
            played = s.get("Played") or (18 if s.get("HasRoundScore") else 0)
            active = bool(s.get("HasRoundScore")) or played > 0
            if not active:
                continue  # not started this round: carry what's accumulated
            rtp, topar = s.get("RoundtoPar"), s.get("ToPar")
            if rtp is not None:
                rec["cur"] = (rec["cur"] or 0.0) + float(rtp)
                rec["holes"] += played
            elif topar is not None:
                # no per-round score published: fall back to the sheet's total
                rec["cur"] = float(topar)
                rec["holes"] = (rnd - 1) * 18 + played

    out: dict[int, dict] = {}
    for pdga, r in state.items():
        if r["wd"]:
            continue
        cur, holes = r["cur"], r["holes"]
        if cur is None:  # no activity on any sheet
            if latest > 1:
                continue  # mid-event never-started: not playing (DNS)
            cur, holes = 0.0, 0  # round 1 loaded, not yet teed off: seed from scratch
        out[pdga] = {
            "name": r["name"],
            "rating": r["rating"],
            "cur": float(cur),
            "rem": max(total_rounds * 18 - holes, 0) / 18.0,
            # Holes played, carried explicitly. It cannot be recovered from
            # `rem` downstream: `rem` is measured against this event's real
            # round list, while every consumer's round count is the per-class
            # constant (3, or 4 for a major). Ledgestone plays four rounds as
            # an elite_plus event, so there the two differ by a whole round.
            "thru": int(holes),
            "place": r["place"],   # current standing per the sheet (None pre-tee)
        }
    return out or None


def live_state(tournament_id: int, division: str) -> dict[int, tuple[float, float]] | None:
    """Back-compat: {pdga: (current_to_par, rounds_remaining)}."""
    field = live_field(tournament_id, division)
    return {p: (v["cur"], v["rem"]) for p, v in field.items()} if field else None


def final_results(tournament_id: int, division: str, *, use_cache: bool = True) -> list[dict]:
    """Finishing order for a completed event.

    Returns [{pdga_number, name, rating, place, round_played}] sorted by
    place. DNF/WD players (no posted score in some regular round) are
    excluded — DGPT awards standings points to finishers only.
    """
    RESULTS_CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = RESULTS_CACHE / f"{tournament_id}_{division}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    event = fetch_event(tournament_id)
    end = event.get("EndDate")
    completed = bool(end) and date.fromisoformat(end) < date.today()

    div = next((d for d in (event.get("Divisions") or []) if d["Division"] == division), None)
    if div is None:
        return []
    # The finishing order lives on the last round the field PLAYED. A
    # sudden-death playoff is a separate sheet carrying only the tied players,
    # so LatestRound can point past the real final round; reading it as the
    # results sheet returns one or two rows, or none once the DNF filter below
    # is applied (DGPT Discmania Challenge banked zero finishers this way,
    # 2026-08-09 — the event vanished from the site entirely).
    rounds = _real_rounds(event, div.get("LatestRound") or event.get("FinalRound"))
    if not rounds:
        return []
    final_round = rounds[-1]
    scores = fetch_round(tournament_id, division, final_round, cache=completed).get("scores") or []

    # DNF detection: DGPT events have no cut in the regular rounds (ids 1..10),
    # so a finisher must post a score in every one of them. Withdrawn players
    # keep a RunningPlace in the live data but earn no standings points.
    #
    # Only regular rounds are intersected. A finals round (ids 11+) is played
    # by qualifiers alone — USWDGC's finals sheet carries 44 of 77 — so
    # requiring it would disqualify every non-finalist. And the round list is
    # already playoff-free: a playoff sheet posts no round scores, so
    # intersecting one in empties the set and disqualifies the whole field,
    # which is precisely how the Discmania Challenge banked nobody.
    finished: set[int] | None = None
    for rnum in (r for r in rounds if r <= 10):
        try:
            rd_scores = (scores if rnum == final_round
                         else fetch_round(tournament_id, division, rnum, cache=completed).get("scores") or [])
        except urllib.error.HTTPError as e:
            if e.code == 404:  # sheet never published (restructured schedule)
                continue
            raise
        if not rd_scores:
            continue
        posted = {s["PDGANum"] for s in rd_scores if s.get("HasRoundScore")}
        if not posted:
            continue  # sheet published but the round hasn't been played yet
        finished = posted if finished is None else finished & posted

    out = []
    for s in scores:
        if not s.get("RunningPlace"):
            continue
        if finished is not None and s.get("PDGANum") not in finished:
            continue
        # 999 = withdrew after qualifying for the finals (still "placed" in
        # live data, but a DNF officially)
        if str(s.get("GrandTotal")) == "999":
            continue
        out.append(
            {
                "pdga_number": s.get("PDGANum"),
                "name": s.get("Name"),
                "rating": s.get("Rating"),
                "place": s.get("RunningPlace"),
                "round_played": final_round,
            }
        )
    out.sort(key=lambda x: x["place"])

    if completed:  # in-progress results still change; don't freeze them
        cache_file.write_text(json.dumps(out), encoding="utf-8")
    return out
