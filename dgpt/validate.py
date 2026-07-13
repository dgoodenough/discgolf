"""Continuous validation: diff our computed standings against StatMando.

StatMando administers the official points, so any mismatch means either our
engine drifted (rule change, payload change, new edge case) or their site is
mid-update. Run after the weekly refresh:

    python -m dgpt.validate

Exit 1 if any matched player's total differs — CI marks the run failed and
GitHub notifies the owner, without blocking the data commit (which happens
first). Name-only mismatches (spelling variants between PDGA Live and
StatMando) are warnings, not failures.
"""
from __future__ import annotations

import html
import re
import sys
import time
import urllib.request

from . import standings

STATMANDO = "https://statmando.com/rankings/dgpt/{div}"
UA = {"User-Agent": "Mozilla/5.0 (dgpt-forecast validation; github.com/dgoodenough/discgolf)"}
TOLERANCE = 0.02  # points; StatMando displays 2 decimals
MIN_ROWS = 50      # fewer parsed rows than this = page empty / mid-update / reshaped
EMPTY_TABLE_TR = 5  # <= this many raw <tr> = table not published (header + footnote)
FETCH_ATTEMPTS = 4
FETCH_RETRY_WAIT = 120  # seconds; rankings pages are briefly empty while they ingest


def statmando_totals(division: str) -> tuple[dict[str, float], int]:
    """Parsed {name: points} plus the page's raw <tr> count. The row count
    separates 'their table is empty' (a handful of rows: header + footnote,
    observed 2026-07-13 while FPO awaited TD reports) from 'rows exist but we
    can't parse them' (a real layout change)."""
    req = urllib.request.Request(STATMANDO.format(div=division.lower()), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S)
    out: dict[str, float] = {}
    for row in rows:
        cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 4 and cells[3].replace(".", "").isdigit():
            name = re.sub(r"\s*\*\s*$", "", cells[2]).strip()
            out[name] = float(cells[3])
    return out, len(rows)


def check(division: str) -> tuple[list[str], list[str], bool]:
    """Returns (errors, warnings, skipped) for one division."""
    # Retry a thin parse: the Monday run lands while StatMando ingests the
    # weekend's results, and a division's page can be briefly empty (observed
    # 2026-07-13: MPO parsed fine while FPO returned 0 rows minutes after the
    # weekend finished). Only a *persistently* thin page matters — and then
    # the raw row count says whether their table simply isn't published yet
    # (skip: no reference to check against, nothing known about our engine)
    # or rows exist that we can no longer parse (error: layout changed).
    for attempt in range(FETCH_ATTEMPTS):
        official, n_tr = statmando_totals(division)
        if len(official) >= MIN_ROWS:
            break
        if attempt < FETCH_ATTEMPTS - 1:
            print(f"  {division}: parse returned {len(official)} rows — "
                  f"retrying in {FETCH_RETRY_WAIT}s (their site may be mid-update)")
            time.sleep(FETCH_RETRY_WAIT)
    if len(official) < MIN_ROWS:
        if n_tr <= EMPTY_TABLE_TR:  # their table isn't published (e.g. awaiting TD reports)
            return [], [f"{division}: StatMando table is empty ({n_tr} rows) — "
                        "standings not published yet (their note: TD reports land "
                        "Sunday night through midweek); cross-check skipped"], True
        return [f"{division}: StatMando has {n_tr} table rows but only {len(official)} parsed "
                f"after {FETCH_ATTEMPTS} attempts — page layout may have changed"], [], False
    ours = {r["name"]: r["points"] for r in standings.compute(division)}

    errors, warnings = [], []
    matched = 0
    for name, pts in official.items():
        if name not in ours:
            if pts > 0:
                warnings.append(f"{division}: '{name}' ({pts}) not matched by name (spelling variant?)")
            continue
        matched += 1
        if abs(pts - ours[name]) > TOLERANCE:
            errors.append(f"{division}: {name} official={pts} ours={ours[name]} (diff {pts - ours[name]:+.2f})")
    if matched < 50:
        errors.append(f"{division}: only {matched} names matched — something is structurally wrong")
    return errors, warnings, False


def main() -> None:
    all_errors: list[str] = []
    skipped: list[str] = []
    for division in ("MPO", "FPO"):
        errors, warnings, skip = check(division)
        matched_msg = ("SKIPPED (reference not published)" if skip
                       else "OK" if not errors else f"{len(errors)} ISSUE(S)")
        print(f"{division}: {matched_msg}")
        for w in warnings[:10]:
            print(f"  warn: {w}")
        for e in errors[:20]:
            print(f"  ERROR: {e}")
        all_errors += errors
        if skip:
            skipped.append(division)
    if all_errors:
        print(f"\nvalidation FAILED: {len(all_errors)} diffs vs StatMando — engine drift or their site mid-update")
        sys.exit(1)
    if skipped:
        print(f"\nvalidation passed with skips: {', '.join(skipped)} unavailable on StatMando "
              "(table not published) — checked divisions match exactly")
    else:
        print("\nvalidation passed: our standings match StatMando exactly")


if __name__ == "__main__":
    main()
