#!/usr/bin/env bash
#
# Publish docs/ to the `site` branch as a single parentless commit.
#
# The published bundle is ~1.6MB of JSON (docs/data/mpo.json alone is ~950KB,
# one line) and it changes every few minutes during play. Committing it to
# main stored a fresh copy of the entire payload in history on every refresh:
# 48 of the last 50 commits on main were machine output, and .git had grown an
# order of magnitude larger than the source it was supposed to be tracking.
#
# `git commit-tree` with no -p makes a parentless commit, force-pushed over the
# branch each time, so `site` always holds exactly one commit: the current site
# and nothing before it. Main keeps only source and the cross-run state the
# pipeline reads back (data/live_signature.txt, data/current_ratings.json,
# predictions/history_*.csv).
#
# ONE-TIME SETUP, AND THE ORDER MATTERS — `site` has to exist before the Pages
# settings dropdown will offer it, and nothing creates it until this script has
# run once:
#
#   1. Actions -> "Refresh forecast" -> Run workflow. Its publish step calls
#      this script, and the force-push below creates the ref, so there is no
#      branch to make by hand and nothing to seed it with.
#   2. Settings -> Pages -> Build and deployment -> Deploy from a branch ->
#      `site` / docs. The dropdown does not list `site` until step 1 has run.
#
# Between the two steps Pages keeps serving main/docs, so the site freezes at
# the last bundle committed there rather than breaking visibly — which is why
# step 2 is easy to forget and worth confirming.
#
# A temporary index is used so the working tree and the real index are never
# touched — the live-refresh loop commits pipeline state to main around these
# calls and must not see staged changes appear underneath it.
set -euo pipefail

branch="${1:-site}"
index="$(mktemp -u "${TMPDIR:-/tmp}/publish-index.XXXXXX")"
trap 'rm -f "$index"' EXIT

# --force because docs/data is gitignored on main and still must be published
GIT_INDEX_FILE="$index" git add --force --all docs
tree="$(GIT_INDEX_FILE="$index" git write-tree)"
commit="$(git commit-tree "$tree" -m "Publish site ($(date -u +'%F %H:%MZ'))")"

for backoff in 0 2 4 8 16; do
  [ "$backoff" = 0 ] || sleep "$backoff"
  if git push --force origin "$commit:refs/heads/$branch"; then
    echo "published tree $tree to $branch as $commit"
    exit 0
  fi
  echo "push to $branch failed; retrying in $((backoff * 2 > 0 ? backoff * 2 : 2))s" >&2
done

echo "could not publish to $branch after retries" >&2
exit 1
