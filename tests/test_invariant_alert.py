"""The publish-gate alert decision: .github/invariant-alert.sh.

This script is what stops a bad live row from taking the loop down every six
minutes. On 2026-08-29 two PDGA rows at Worlds carried impossible cumulative
scores — MPO Sander Bahnerth cur=+981, FPO Samantha Zaborowski cur=+849 — and
each one published, alerted, and killed its run. The de-dupe that was supposed
to make the second sighting quiet could not work: its marker lived in
data/cache/, which only actions/cache carries between runs, and that save is
skipped when the job fails. The alerting run is always the failing run.

So the marker moved into tracked state, and the contract this file pins down
is the episode semantics: alert once when a violation set appears, stay quiet
while it persists, and alert again if it clears and comes back.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / ".github" / "invariant-alert.sh"

CLEAN = 0
NEW = 3
BROKEN = 1

# Real marker lines, in the shape dgpt/invariants.py writes: stable keys with
# no scores in them, which is what makes byte-comparison a valid de-dupe.
BAHNERTH = "97344 MPO bounds cur Sander Bahnerth\n"
ZABOROWSKI = "97344 FPO bounds cur Samantha Zaborowski\n"


@pytest.fixture
def run(tmp_path):
    viol = tmp_path / "cache" / "invariant_violations.txt"
    alerted = tmp_path / "invariant_alerted.txt"
    viol.parent.mkdir(parents=True)

    def go(violations: str | None):
        """Write the violations file (None = the refresh wrote none) and decide."""
        if violations is None:
            viol.unlink(missing_ok=True)
        else:
            viol.write_text(violations, encoding="utf-8")
        proc = subprocess.run(
            [str(SCRIPT), str(viol), str(alerted)],
            capture_output=True, text=True, timeout=30,
        )
        marker = alerted.read_text(encoding="utf-8") if alerted.exists() else None
        return proc, marker

    return go


def test_clean_run_is_quiet(run):
    proc, marker = run("")
    assert proc.returncode == CLEAN, proc.stderr
    assert "clean" in proc.stdout


def test_first_violation_alerts_and_records_it(run):
    proc, marker = run(BAHNERTH)
    assert proc.returncode == NEW, proc.stderr
    assert "NEW violations" in proc.stdout
    assert marker == BAHNERTH


def test_same_violation_stays_quiet(run):
    """The whole point: a persistent bad row must not re-kill the loop."""
    assert run(BAHNERTH)[0].returncode == NEW
    proc, marker = run(BAHNERTH)
    assert proc.returncode == CLEAN, proc.stderr
    assert "already alerted" in proc.stdout
    assert marker == BAHNERTH


def test_a_different_violation_alerts_again(run):
    """08-29 saw two distinct rows hours apart; the second is real news."""
    assert run(BAHNERTH)[0].returncode == NEW
    proc, marker = run(ZABOROWSKI)
    assert proc.returncode == NEW, proc.stderr
    assert marker == ZABOROWSKI


def test_a_violation_added_alongside_an_existing_one_alerts(run):
    assert run(BAHNERTH)[0].returncode == NEW
    both = BAHNERTH + ZABOROWSKI
    proc, marker = run(both)
    assert proc.returncode == NEW, proc.stderr
    assert marker == both


def test_recurrence_after_a_clean_run_alerts_again(run):
    """Alert once per episode, not once per season. If PDGA fixes a row and
    then breaks it again, the second break has to be seen."""
    assert run(BAHNERTH)[0].returncode == NEW
    proc, marker = run("")
    assert proc.returncode == CLEAN
    assert marker == ""                      # cleared, so a recurrence is news
    assert run(BAHNERTH)[0].returncode == NEW


def test_missing_violations_file_is_not_an_alert(run):
    """A refresh that died before writing the marker is reported by its own
    path; absence of evidence must not be published as evidence of a fault."""
    proc, _ = run(None)
    assert proc.returncode == CLEAN, proc.stderr
    assert "nothing to compare" in proc.stdout


def test_unusable_marker_path_reports_broken_rather_than_quiet(tmp_path):
    """Exit 1 is distinct from 'clean' so the caller can tell a failed check
    from a passing one — swallowing this would silently disable the gate.

    The unusable path is a marker whose parent is a regular file. Chmod-based
    denial would not do: CI runs as a normal user but this suite is also run
    as root, where the mode bits are simply ignored and the test would pass
    locally for the wrong reason."""
    viol = tmp_path / "violations.txt"
    viol.write_text(BAHNERTH, encoding="utf-8")
    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("", encoding="utf-8")
    proc = subprocess.run(
        [str(SCRIPT), str(viol), str(not_a_dir / "invariant_alerted.txt")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == BROKEN, proc.stdout
    assert "cannot create" in proc.stderr
