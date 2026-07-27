"""Capture raw PDGA live-API payloads into tests/fixtures/payloads/.

Each file is the COMPLETE, unmodified API response (the {"data": ...}
envelope), so tests can serve it straight through a mocked live_api._get.
Re-run after adding an event to EVENTS to (re)capture its sheets:

    python tests/fixtures/capture.py

Completed events never change upstream, so committed captures are stable;
recapturing an already-present file is skipped unless --force is given.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dgpt import live_api  # noqa: E402

PAYLOADS = Path(__file__).parent / "payloads"

# (tournament_id, division, rounds) — captured after each event completed
EVENTS = [
    # DGPT JomezPro Champions Landing Open 2026: multi-layout event whose
    # per-sheet round-total ToPar values are mutually incomparable — the
    # regression behind "Sum per-round RoundtoPar; never trust ToPar across
    # round sheets" (b4cfb56, model had the winner 100% to finish 2nd).
    (100195, "MPO", (1, 2, 3)),
    # USWDGC 2026 (FPO-only major): populates RoundtoPar rather than a live
    # ToPar (6e99dd4), and the weather-delay weekend behind "Build the live
    # field from all round sheets, not just the latest" (5094f16). Regular
    # rounds are 1-3; the finals sheet carries PDGA Live's finals round id 12
    # (FinalRound=12; there is no round 4 and no round 11 sheet), and round 13
    # is a one-row sudden-death playoff sheet.
    (97341, "FPO", (1, 2, 3, 12, 13)),
]


def save(name: str, url: str, force: bool) -> None:
    out = PAYLOADS / f"{name}.json"
    if out.exists() and not force:
        print(f"  {name}: already captured")
        return
    raw = live_api._get(url)
    out.write_text(json.dumps(raw, separators=(",", ":")), encoding="utf-8")
    print(f"  {name}: {out.stat().st_size // 1024} KB")


def main() -> None:
    force = "--force" in sys.argv
    PAYLOADS.mkdir(parents=True, exist_ok=True)
    for tid, div, rounds in EVENTS:
        save(f"event_{tid}", f"{live_api.BASE}/live_results_fetch_event?TournID={tid}", force)
        for r in rounds:
            save(
                f"round_{tid}_{div}_{r}",
                f"{live_api.BASE}/live_results_fetch_round?TournID={tid}&Division={div}&Round={r}",
                force,
            )


if __name__ == "__main__":
    main()
