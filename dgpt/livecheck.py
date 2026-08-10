"""Cheap change-detector for the frequent live cron.

Runs before the heavy refresh (no numpy) and decides whether anything worth
re-simulating has changed since last time: a live event's scores moved, or an
event's completed/live status flipped. If nothing changed it signals a skip,
so the 15-minute cron effectively idles between rounds and overnight instead
of re-running the full simulation every time.

Writes `changed=true|false` to $GITHUB_OUTPUT (and prints it). On a change it
updates data/live_signature.txt, which the refresh commit then persists.
"""
from __future__ import annotations

import hashlib
import os
import sys

from . import config, live_api, ratings, schedule

STATE_SIG = config.DATA_DIR / "live_signature.txt"


def signature() -> str:
    """Fingerprint of what would change the forecast: each event's
    completed/live status, every live player's current score, and the
    current official ratings snapshot (PDGA updates it ~monthly; the
    fetch behind it is TTL-throttled so this check stays cheap)."""
    rows = schedule.load()
    # Which events are in progress is schedule.live_events()' judgement, not a
    # date comparison of our own. It carries a grace day past the end date
    # because a US Sunday final round runs past 00:00 UTC, and re-deriving
    # `start <= today <= end` here threw that away: at midnight UTC, mid-final
    # round, this stopped hashing live scores entirely, so every later check
    # reported "unchanged" and the site froze with the lead card mid-round —
    # the same failure the grace day was added to fix (Ledgestone 2026,
    # repeated at the Discmania Challenge on 2026-08-10).
    live_tids = {r["tournament_id"] for r in schedule.live_events(rows)}
    parts: list[str] = [f"ratings:{ratings.signature_component()}"]
    for r in rows:
        completed = r["completed"]
        live = r["tournament_id"] in live_tids
        parts.append(f"{r['tournament_id']}:{int(completed)}:{int(live)}")
        if live:
            for div in ("MPO", "FPO"):
                if not r[div.lower()]:
                    continue
                field = live_api.live_field(r["tournament_id"], div) or {}
                for pdga in sorted(field):
                    parts.append(f"{r['tournament_id']}:{div}:{pdga}:{field[pdga]['cur']}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def main() -> None:
    sig = signature()
    old = STATE_SIG.read_text().strip() if STATE_SIG.exists() else ""
    changed = sig != old
    if changed:
        STATE_SIG.write_text(sig + "\n")

    print("changed" if changed else "unchanged (skipping refresh)")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()
    sys.exit(0)
