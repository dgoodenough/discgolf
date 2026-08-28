"""The live loop's hand-off: .github/dispatch-successor.sh.

This is the script that keeps the site updating across GitHub's 6h per-job
ceiling. It replaced an assumption that failed in production on 2026-08-27 —
that the `*/15` cron would have left a queued successor in the concurrency
group — so the thing worth testing is that it never reports success without a
run to show for it. A dispatch that returns 204 and enqueues nothing is a real
observed failure (2026-08-26), and it is indistinguishable from a good one at
the call site.

`gh` is stubbed by a script on PATH: the run-id file it reads is the whole
world model. Polling is driven to zero delay through the SUCCESSOR_* knobs the
script exposes for exactly this purpose.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / ".github" / "dispatch-successor.sh"

STUB_GH = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "run list")
    cat "$STUB_STATE/newest" 2>/dev/null
    ;;
  "workflow run")
    if [ -f "$STUB_STATE/fail_once" ]; then
      rm -f "$STUB_STATE/fail_once"
      echo "stub: dispatch refused" >&2
      exit 1
    fi
    # Whether the accepted dispatch actually produces a run is the variable
    # under test: STUB_APPEAR=0 reproduces the accepted-then-dropped case.
    if [ "${STUB_APPEAR:-1}" = "1" ]; then
      echo $(( $(cat "$STUB_STATE/newest") + 1 )) > "$STUB_STATE/newest"
    fi
    ;;
esac
exit 0
"""


@pytest.fixture
def harness(tmp_path):
    """A stubbed `gh` on PATH plus a runner for the script under test."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(STUB_GH, encoding="utf-8")
    gh.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()
    (state / "newest").write_text("1000\n", encoding="utf-8")
    log = tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")

    def run(*inputs: str, appear: bool = True, fail_once: bool = False, **env_over):
        if fail_once:
            (state / "fail_once").write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "GH_LOG": str(log),
            "STUB_STATE": str(state),
            "STUB_APPEAR": "1" if appear else "0",
            "GH_TOKEN": "stub-token",
            "GITHUB_REPOSITORY": "dgoodenough/discgolf",
            # No sleeping in tests; two attempts, two polls each.
            "SUCCESSOR_ATTEMPTS": "2",
            "SUCCESSOR_POLL_TRIES": "2",
            "SUCCESSOR_POLL_SECS": "0",
            **env_over,
        }
        proc = subprocess.run(
            [str(SCRIPT), *inputs], capture_output=True, text=True, env=env, timeout=60,
        )
        return proc, log.read_text(encoding="utf-8")

    return run


def _dispatches(gh_log: str) -> list[str]:
    return [ln for ln in gh_log.splitlines() if ln.startswith("workflow run")]


def test_hands_off_and_confirms_the_new_run(harness):
    proc, gh_log = harness()
    assert proc.returncode == 0, proc.stderr
    assert "successor queued: run 1001" in proc.stdout
    assert len(_dispatches(gh_log)) == 1
    assert "workflow run live-refresh.yml --repo dgoodenough/discgolf --ref main" in gh_log


def test_passes_workflow_inputs_through(harness):
    """The post-failure retry is marked so the chain stops at one hop; if the
    flag does not reach the successor, a persistent crash loops forever."""
    proc, gh_log = harness("after_failure=true")
    assert proc.returncode == 0, proc.stderr
    assert "-f after_failure=true" in gh_log


def test_fails_loudly_when_the_dispatch_is_accepted_but_drops(harness):
    """The 2026-08-26 shape: 204 accepted, nothing enqueued. Silence here is
    the outage, so the script must exhaust its retries and exit non-zero."""
    proc, gh_log = harness(appear=False)
    assert proc.returncode == 1
    assert "::error::" in proc.stdout
    assert "Could not hand off to a successor" in proc.stdout
    assert len(_dispatches(gh_log)) == 2      # SUCCESSOR_ATTEMPTS


def test_retries_when_the_dispatch_call_itself_fails(harness):
    """A refused `gh workflow run` is worth one more try before giving up."""
    proc, gh_log = harness(fail_once=True)
    assert proc.returncode == 0, proc.stderr
    assert len(_dispatches(gh_log)) == 2
    assert "successor queued" in proc.stdout


def test_reports_the_run_it_actually_saw(harness):
    """The confirmation names the new run id, so a hand-off can be audited
    from the log of the run that made it rather than inferred."""
    proc, _ = harness()
    assert "run 1001" in proc.stdout


def test_survives_an_unreadable_baseline(harness, tmp_path):
    """If the pre-dispatch run list cannot be read, the dispatch still has to
    happen — an unknown baseline is not a reason to skip the hand-off."""
    (tmp_path / "state" / "newest").unlink()
    proc, gh_log = harness()
    assert proc.returncode == 0, proc.stderr
    assert len(_dispatches(gh_log)) == 1


def test_runs_without_an_explicit_repo(harness):
    """Outside Actions there is no GITHUB_REPOSITORY and `gh` infers the repo
    from the remote. The point of the case is the shell: `--repo` is built as
    an array that is empty here, and an empty array expanded under `set -u`
    aborts on bash before 4.4. Runners are 5.x, but the script is also run by
    hand."""
    proc, gh_log = harness(GITHUB_REPOSITORY="", GH_REPO="")
    assert proc.returncode == 0, proc.stderr
    assert "--repo" not in gh_log
