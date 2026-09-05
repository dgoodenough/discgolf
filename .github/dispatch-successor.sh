#!/usr/bin/env bash
#
# Hand the live-refresh loop off to a fresh run of itself, and prove it landed.
#
# The loop yields at ~5.5h to stay under GitHub's 6h per-job ceiling, and used
# to depend on the `*/15` cron having left a queued successor in the shared
# concurrency group during that window. On 2026-08-27 it had not: run #1019
# yielded at 20:01Z announcing "the queued successor takes over" and nothing
# took over. GitHub delivered ZERO scheduled fires for the workflow between
# 14:30Z and 23:17Z, when the loop was restarted by hand — 3h16m with no live
# updates during round 2 of Worlds, and 5h38m earlier the same day (08:52Z ->
# 14:30Z). Corroborating the scheduler as the culprit rather than the queue:
# refresh.yml's `0 11 * * *` cron fired that day at 20:57Z, ~10 hours late.
#
# So the hand-off no longer waits for a cron fire that may never come — the
# yielding run dispatches its own successor. `workflow_dispatch` (with
# `repository_dispatch`) is the documented exception to the rule that events
# triggered by GITHUB_TOKEN do not create a new workflow run, so this needs no
# PAT; it needs `actions: write`, which live-refresh.yml now requests.
#
# The successor is dispatched BEFORE the current run exits, so it lands in the
# concurrency group as the pending run and starts the instant the slot frees —
# the same shape the cron was supposed to produce, minus the hoping.
#
# Verification is the point, not a nicety. On 2026-08-26 an Actions incident
# left dispatches returning 204 and then silently dropping: accepted, never
# enqueued. A hand-off that reports success without a run to show for it is
# exactly the failure this script exists to prevent, so it polls for the new
# run, re-dispatches once, and only then gives up — loudly, non-zero.
#
# Usage:  dispatch-successor.sh [key=value ...]
#   key=value pairs are passed through as workflow inputs (`gh -f key=value`).
#
# Env:
#   GH_TOKEN             required; the workflow's github.token is enough
#   GITHUB_REPOSITORY    owner/repo (set by Actions; falls back to gh's remote)
#   SUCCESSOR_WORKFLOW   workflow to dispatch      (default live-refresh.yml)
#   SUCCESSOR_REF        ref to dispatch it on     (default main)
#   SUCCESSOR_ATTEMPTS   dispatch attempts         (default 2)
#   SUCCESSOR_POLL_TRIES polls per attempt         (default 10)
#   SUCCESSOR_POLL_SECS  seconds between polls     (default 3)
# The four tunables exist so the test suite can drive this without sleeping.
#
# Deliberately not `set -e`: every failure here is handled and reported, and
# the caller decides what a failed hand-off means for its own exit status.
set -uo pipefail

workflow="${SUCCESSOR_WORKFLOW:-live-refresh.yml}"
ref="${SUCCESSOR_REF:-main}"
attempts="${SUCCESSOR_ATTEMPTS:-2}"
poll_tries="${SUCCESSOR_POLL_TRIES:-10}"
poll_secs="${SUCCESSOR_POLL_SECS:-3}"

repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
repo_args=()
[ -n "$repo" ] && repo_args=(--repo "$repo")

input_args=()
for pair in "$@"; do
  input_args+=(-f "$pair")
done

# Newest run id for this workflow. Run ids increase, and the current run is
# already in the list, so "the newest id changed" is a sufficient and cheap
# test for "a new run exists" without needing to identify it precisely.
newest_run_id() {
  gh run list "${repo_args[@]}" --workflow "$workflow" --limit 1 \
    --json databaseId --jq '.[0].databaseId' 2>/dev/null
}

before="$(newest_run_id)"
if [ -z "$before" ]; then
  # Not fatal on its own: we can still dispatch, we just cannot confirm by
  # comparison. Treat an unreadable baseline as "nothing seen yet" so any id
  # appearing below counts as the successor.
  echo "warning: could not read the current newest run id for $workflow" >&2
  before=""
fi

for attempt in $(seq 1 "$attempts"); do
  if gh workflow run "$workflow" "${repo_args[@]}" --ref "$ref" "${input_args[@]}"; then
    echo "dispatch attempt $attempt: accepted"
  else
    echo "dispatch attempt $attempt: gh workflow run failed" >&2
    continue
  fi

  # A 204 is not a run. Poll until one actually shows up.
  for _ in $(seq 1 "$poll_tries"); do
    sleep "$poll_secs"
    now="$(newest_run_id)"
    if [ -n "$now" ] && [ "$now" != "$before" ]; then
      echo "successor queued: run $now (dispatched $workflow on $ref)"
      exit 0
    fi
  done
  echo "dispatch attempt $attempt: accepted but no new run appeared" >&2
done

echo "::error::Could not hand off to a successor run of $workflow after $attempts attempts — live updates stop here until the schedule restarts the loop."
exit 1
