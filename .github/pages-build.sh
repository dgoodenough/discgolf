#!/usr/bin/env bash
#
# Trigger the legacy Pages build for the `site` branch and wait for it.
#
# Bot pushes (GITHUB_TOKEN) don't auto-trigger legacy Pages, so the workflows
# request a build explicitly. A build occasionally errors ("try again later")
# and needs a fresh request — but a build that is merely SLOW must be left
# alone: every POST to /pages/builds starts a new "pages build and
# deployment" run, which cancels the one in flight.
#
# That is what happened on 2026-11-06: GitHub's deploy step sat in
# `updating_pages` for 10+ minutes, the old loop gave each build ~3 minutes
# and then re-triggered, and so each request cancelled the deploy before it —
# three times per run, across two runs — and nothing went live.
#
# So: request once, poll for up to WAIT_MIN minutes, and re-request only on
# `errored` (at most MAX_TRIGGERS times in all). Never fails the job: the site
# branch is already updated, and Pages picks it up on the next build anyway.
set -uo pipefail

repo="${GITHUB_REPOSITORY:?}"
WAIT_MIN="${WAIT_MIN:-20}"
MAX_TRIGGERS="${MAX_TRIGGERS:-3}"

trigger() { gh api -X POST "repos/$repo/pages/builds" >/dev/null || echo "build request failed (will keep polling)"; }

triggers=1
trigger
deadline=$(( $(date +%s) + WAIT_MIN * 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 15
  status=$(gh api "repos/$repo/pages/builds/latest" --jq .status 2>/dev/null || echo "unknown")
  echo "$(date -u +%H:%M:%S) trigger $triggers: $status"
  case "$status" in
    built) exit 0 ;;
    errored)
      if [ "$triggers" -ge "$MAX_TRIGGERS" ]; then break; fi
      triggers=$(( triggers + 1 ))
      trigger ;;
  esac
done
echo "::warning::Pages did not reach 'built' within ${WAIT_MIN} min (${triggers} request(s)); the site branch is updated and the next build will pick it up"
exit 0
