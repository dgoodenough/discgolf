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
import urllib.request

from . import standings

STATMANDO = "https://statmando.com/rankings/dgpt/{div}"
UA = {"User-Agent": "Mozilla/5.0 (dgpt-forecast validation; github.com/dgoodenough/discgolf)"}
TOLERANCE = 0.02  # points; StatMando displays 2 decimals


def statmando_totals(division: str) -> dict[str, float]:
    req = urllib.request.Request(STATMANDO.format(div=division.lower()), headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    out: dict[str, float] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", " ", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 4 and cells[3].replace(".", "").isdigit():
            name = re.sub(r"\s*\*\s*$", "", cells[2]).strip()
            out[name] = float(cells[3])
    return out


def check(division: str) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) for one division."""
    official = statmando_totals(division)
    if len(official) < 50:  # page shape changed or partial load
        return [f"{division}: StatMando parse produced only {len(official)} rows — page layout may have changed"], []
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
    return errors, warnings


def main() -> None:
    all_errors: list[str] = []
    for division in ("MPO", "FPO"):
        errors, warnings = check(division)
        matched_msg = "OK" if not errors else f"{len(errors)} POINT DIFFS"
        print(f"{division}: {matched_msg}")
        for w in warnings[:10]:
            print(f"  warn: {w}")
        for e in errors[:20]:
            print(f"  ERROR: {e}")
        all_errors += errors
    if all_errors:
        print(f"\nvalidation FAILED: {len(all_errors)} diffs vs StatMando — engine drift or their site mid-update")
        sys.exit(1)
    print("\nvalidation passed: our standings match StatMando exactly")


if __name__ == "__main__":
    main()
