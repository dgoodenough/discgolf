"""Results fetcher using PDGA's public live-scoring API (no auth required).

live_results_fetch_event gives divisions + final round number; the final
round's scores carry RunningPlace = finishing place. Requests are throttled
and completed-event responses are cached to disk since they never change.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from . import config

BASE = "https://www.pdga.com/apps/tournament/live-api"
UA = {"User-Agent": "dgpt-forecast/1.0 (github.com/dgoodenough/discgolf)"}
LIVE_CACHE = config.CACHE_DIR / "live"
RESULTS_CACHE = config.CACHE_DIR / "results"

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
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    _memo[url] = data
    return data


def fetch_event(tournament_id: int, *, cache: bool = False) -> dict:
    cf = LIVE_CACHE / f"event_{tournament_id}.json" if cache else None
    return _get(f"{BASE}/live_results_fetch_event?TournID={tournament_id}", cf)["data"]


def fetch_round(tournament_id: int, division: str, round_num: int, *, cache: bool = False) -> dict:
    cf = LIVE_CACHE / f"round_{tournament_id}_{division}_{round_num}.json" if cache else None
    url = f"{BASE}/live_results_fetch_round?TournID={tournament_id}&Division={division}&Round={round_num}"
    return _get(url, cf)["data"]


def event_complete(tournament_id: int, divisions: tuple[str, ...] = ("MPO", "FPO")) -> bool:
    """True once every (non-withdrawn) player in each relevant division has a
    final-round score — so the event can be banked into the standings the
    moment it finishes rather than waiting for the date to pass. Conservative:
    if it can't confirm, returns False and the date-based fallback applies.

    The event-level "HighestCompletedRound" is unreliable here (it advances
    when the fastest division finishes, while another may still be on course),
    so we check each division's final round directly.
    """
    event = fetch_event(tournament_id)
    final = event.get("FinalRound")
    if not final:
        return False
    present = {d["Division"] for d in event["Divisions"]}
    for div in divisions:
        if div not in present:
            continue
        d = next(x for x in event["Divisions"] if x["Division"] == div)
        if d.get("LatestRound") != final:
            return False  # not on the final round yet
        scores = fetch_round(tournament_id, div, final).get("scores") or []
        if not scores:
            return False
        for s in scores:
            if s.get("HasRoundScore") or str(s.get("GrandTotal")) == "999":
                continue  # finished, or withdrawn
            if (s.get("Played") or 0) > 0:
                return False  # mid-round — still on the course
            # played 0 holes with no score: cut / not in the final round, ignore
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
    row before their debut event."""
    try:
        scores = fetch_round(tournament_id, division, 1).get("scores") or []
    except (urllib.error.HTTPError, KeyError):
        return {}
    return {
        s["PDGANum"]: {"name": s.get("Name"), "rating": s.get("Rating")}
        for s in scores
        if s.get("PDGANum")
    }


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
    total_rounds = event.get("FinalRound")
    div = next((d for d in event["Divisions"] if d["Division"] == division), None)
    if div is None or not total_rounds:
        return None
    latest = div.get("LatestRound")
    if not latest:
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
    # Per-round score fields vary: DGPT populates the running "ToPar" live
    # mid-round; some majors backfill it only when a round completes and
    # carry it forward on later sheets, with the live score in per-round
    # "RoundtoPar" (0 = not started, so activity is gated on Played /
    # HasRoundScore). A mid-round row can carry BOTH a stale carried ToPar
    # and a live RoundtoPar; since a live ToPar always equals prior + round
    # score, "ToPar unchanged from the prior total while the round is
    # active" identifies the stale case exactly, and the round score is
    # added on top.
    state: dict[int, dict] = {}
    for rnd in range(1, latest + 1):
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
            rec = state.setdefault(pdga, {"name": None, "rating": None, "cur": None, "holes": 0, "wd": False})
            rec["name"] = s.get("Name") or rec["name"]
            rec["rating"] = s.get("Rating") or rec["rating"]
            if str(s.get("GrandTotal")) == "999":
                rec["wd"] = True
            played = s.get("Played") or (18 if s.get("HasRoundScore") else 0)
            active = bool(s.get("HasRoundScore")) or played > 0
            topar, rtp = s.get("ToPar"), s.get("RoundtoPar")
            if topar is not None:
                t = float(topar)
                if (active and not s.get("HasRoundScore") and rtp is not None
                        and rec["cur"] is not None and t == rec["cur"]):
                    t += float(rtp)  # stale carried total mid-round: add the live round score
                rec["cur"] = t
                rec["holes"] = (rnd - 1) * 18 + played
            elif active and rtp is not None:
                rec["cur"] = (rec["cur"] or 0.0) + float(rtp)
                rec["holes"] += played

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

    div = next((d for d in event["Divisions"] if d["Division"] == division), None)
    if div is None:
        return []
    final_round = div.get("LatestRound") or event.get("FinalRound")
    scores = fetch_round(tournament_id, division, final_round, cache=completed).get("scores") or []

    # DNF detection: DGPT events have no cut in regular rounds (1..N; finals
    # use round ids 11/12), so a finisher must post a score in every regular
    # round. Withdrawn players keep a RunningPlace in the live data but earn
    # no standings points.
    finished: set[int] | None = None
    for rnum in range(1, 11):
        if rnum == final_round:
            break
        try:
            rd_scores = fetch_round(tournament_id, division, rnum, cache=completed).get("scores") or []
        except urllib.error.HTTPError as e:
            if e.code == 404:  # past the last regular round
                break
            raise
        if not rd_scores:
            break
        posted = {s["PDGANum"] for s in rd_scores if s.get("HasRoundScore")}
        finished = posted if finished is None else finished & posted
    if final_round <= 10:  # no finals: the last regular round counts too
        posted = {s["PDGANum"] for s in scores if s.get("HasRoundScore")}
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
