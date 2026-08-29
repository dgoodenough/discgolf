#!/usr/bin/env bash
#
# Decide whether the current invariant violations are worth alerting on.
#
# dgpt/invariants.py rewrites data/cache/invariant_violations.txt on every
# refresh (empty = clean). Its lines are stable keys with no scores in them,
# precisely so this comparison is possible: the same bad player-row seen six
# minutes later produces a byte-identical line.
#
# Exit codes, so the caller can order its publish/commit/alert steps around it:
#   0  nothing to alert — clean, or this exact violation set was already alerted
#   3  NEW violations; the caller should go red (and hand off before it dies)
#   1  the script itself could not do its job
#
# Why the alerted marker is NOT kept in data/cache/ (where it used to live):
# that directory is gitignored and carried between runs only by actions/cache,
# whose save runs in a post step — and a job that ends non-zero skips it. The
# alerting run ALWAYS ends non-zero, so the marker written by the run that
# alerts is the one guaranteed never to be saved. Observed 2026-08-29 on runs
# #1027 (MPO Sander Bahnerth cur=+981) and #1029 (FPO Samantha Zaborowski
# cur=+849): both wrote the marker, both showed "Post Restore results cache:
# skipped", and neither left anything behind for the next run to compare
# against. The de-dupe the workflow comment promised could not have worked.
#
# So the marker lives at data/invariant_alerted.txt, tracked in git alongside
# the other cross-run state the pipeline reads back (data/live_signature.txt,
# data/current_ratings.json). The caller must run this BEFORE its state commit
# so the updated marker rides along with it.
#
# A clean run clears the marker. Alerting is once per contiguous episode, not
# once per season: if a bad row disappears and later comes back, that is news
# again.
set -uo pipefail

viol="${1:-data/cache/invariant_violations.txt}"
alerted="${2:-data/invariant_alerted.txt}"

if ! mkdir -p "$(dirname "$alerted")" 2>/dev/null; then
  echo "invariant-alert: cannot create $(dirname "$alerted")" >&2
  exit 1
fi

# No marker file at all means the refresh never got as far as writing one.
# That is not "clean" — it is "unknown" — but it is also not a violation to
# alert on, and the refresh failing is reported by its own path.
if [ ! -f "$viol" ]; then
  echo "invariant-alert: no violations file at $viol — nothing to compare"
  exit 0
fi

if [ ! -s "$viol" ]; then
  if [ -s "$alerted" ]; then
    : > "$alerted" || exit 1
    echo "invariant-alert: violations cleared; marker reset"
  else
    echo "invariant-alert: clean"
  fi
  exit 0
fi

if cmp -s "$viol" "$alerted"; then
  echo "invariant-alert: same violations already alerted; staying quiet"
  exit 0
fi

if ! cp "$viol" "$alerted"; then
  echo "invariant-alert: could not update $alerted" >&2
  exit 1
fi
echo "invariant-alert: NEW violations ($(wc -l < "$viol" | tr -d ' ')); marker updated"
exit 3
