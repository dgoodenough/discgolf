"""Publish-gate sanity checks on live-event data (flag loudly, never block).

Every wrong live number this season shipped through a gap that was visible at
ingest: the Jomez leaderboard inversion (b4cfb56) put the model's reconstructed
totals in a different order than the sheet's own authoritative RunningPlace.
These checks compare what the model is about to publish for each live event
against the sheet's own account of the leaderboard, plus cheap bounds that
catch parsing the wrong field (a to-par of -62, nine "rounds remaining").

Posture matches validate.py: the refresh and its data commit always proceed.
Violations are written to data/cache/invariant_violations.txt and the
workflows turn a non-empty marker into a red run AFTER the publish, so the
owner is notified without the site going stale. Marker lines are stable keys
(no scores in them) so the workflows can de-duplicate alerts; the human-
readable detail goes to the run log.

Runs inside every refresh (the fetches are memoized per process, so it adds
no extra API traffic) and standalone:

    python -m dgpt.invariants [--strict]
"""
from __future__ import annotations

import itertools
import sys

from . import config, live_api, schedule

MARKER = config.CACHE_DIR / "invariant_violations.txt"
CUR_MIN, CUR_MAX = -45.0, 80.0  # event to-par outside this = wrong field parsed
REM_MAX = 6.0    # DGPT events run <= 5 rounds incl. finals; more = bad FinalRound math
MAX_PAIRS = 5    # ordering violations reported per event/division


def check_event(tid: int, division: str) -> list[str]:
    """Stable violation keys for one live event's division ([] when clean)."""
    out: list[str] = []
    field = live_api.live_field(tid, division)
    if not field:
        return out

    for pdga, rec in field.items():
        if not (CUR_MIN <= rec["cur"] <= CUR_MAX):
            out.append(f"{tid} {division} bounds cur {rec['name'] or pdga}")
            print(f"  invariant: {rec['name']} cur={rec['cur']:+g} outside [{CUR_MIN}, {CUR_MAX}] ({tid} {division})")
        if not (0.0 <= rec["rem"] <= REM_MAX):
            out.append(f"{tid} {division} bounds rem {rec['name'] or pdga}")
            print(f"  invariant: {rec['name']} rem={rec['rem']} rounds outside [0, {REM_MAX}] ({tid} {division})")

    # Ordering: our reconstructed totals must agree with the latest sheet's
    # own RunningPlace wherever both exist. A strict inversion (worse score,
    # better place) is exactly the Jomez failure signature.
    event = live_api.fetch_event(tid)
    div = next((d for d in event.get("Divisions", []) if d.get("Division") == division), None)
    latest = div.get("LatestRound") if div else None
    if not latest:
        return out
    try:
        scores = live_api.fetch_round(tid, division, latest).get("scores") or []
    except Exception:
        return out
    place = {
        s["PDGANum"]: int(s["RunningPlace"])
        for s in scores
        if s.get("PDGANum") and s.get("RunningPlace")
    }
    rows = sorted(
        (rec["cur"], place[p], rec["name"] or str(p))
        for p, rec in field.items()
        if p in place
    )
    reported = 0
    for (ca, pa, na), (cb, pb, nb) in itertools.combinations(rows, 2):
        if cb > ca + 1e-9 and pb < pa:  # b scored worse yet placed better
            out.append(f"{tid} {division} inversion {nb} ahead-of {na}")
            if reported < MAX_PAIRS:
                print(f"  invariant: {nb} ({cb:+g}) holds place {pb} ahead of {na} ({ca:+g}, place {pa}) ({tid} {division})")
            reported += 1
    if reported > MAX_PAIRS:
        print(f"  invariant: ... {reported - MAX_PAIRS} more inversions ({tid} {division})")
    return out


def run_checks(write_marker: bool = True) -> list[str]:
    """Check every live points event; write the marker file (empty = clean)."""
    violations: list[str] = []
    for row in schedule.live_events():
        for division in ("MPO", "FPO"):
            if not row[division.lower()]:
                continue
            try:
                violations += check_event(row["tournament_id"], division)
            except Exception as e:
                # a transient fetch failure is not a data violation; just note it
                print(f"  invariant check skipped for {row['tournament_id']} {division}: {e}")
    if write_marker:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text("".join(v + "\n" for v in sorted(violations)), encoding="utf-8")
    if violations:
        print(f"  INVARIANT VIOLATIONS: {len(violations)} (marker: {MARKER})")
    else:
        print("  invariants: clean")
    return violations


def main() -> None:
    strict = "--strict" in sys.argv
    violations = run_checks()
    sys.exit(1 if strict and violations else 0)


if __name__ == "__main__":
    main()
