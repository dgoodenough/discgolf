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

import datetime as dt
import hashlib
import os
import sys

from . import config, live_api, schedule

STATE_SIG = config.DATA_DIR / "live_signature.txt"


def signature() -> str:
    """Fingerprint of what would change the forecast: each event's
    completed/live status plus every live player's current score."""
    rows = schedule.load()
    today = dt.date.today()
    parts: list[str] = []
    for r in rows:
        start = dt.date.fromisoformat(r["start_date"])
        end = dt.date.fromisoformat(r["end_date"])
        completed = end < today
        live = start <= today <= end
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
