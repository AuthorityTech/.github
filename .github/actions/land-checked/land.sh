#!/usr/bin/env bash
# Moves main to HEAD only once that exact commit's Vercel status is success: pushes it to a
# land/ branch, waits for Vercel there, then fast-forwards main to it. When main moved
# meanwhile, rebases and builds again. Never force-pushes main.
set -euo pipefail

workflow=$(basename "${GITHUB_WORKFLOW_REF%@*}")
branch="land/${workflow%.*}/$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"

stop() {
  echo "::error::$1; main untouched, $branch kept"
  exit 1
}

for round in $(seq "$ROUNDS"); do
  sha=$(git rev-parse HEAD)
  git push --force origin "HEAD:refs/heads/$branch" || stop "pushing $branch failed"
  echo "Build $round of $ROUNDS: waiting up to $BUILD_MINUTES min for Vercel on $sha ($branch)"
  deadline=$(( $(date +%s) + BUILD_MINUTES * 60 ))
  last=
  while :; do
    # GitHub lists statuses newest first, so the first Vercel one is its current state.
    state=$(gh api "repos/$GITHUB_REPOSITORY/commits/$sha/statuses?per_page=100" \
      --jq 'map(select(.context == "Vercel"))[0] | if . then "\(.state) \(.target_url)" else "none" end') || state=unreadable
    [ "$state" = "$last" ] || echo "Vercel: $state"
    last=$state
    case $state in
      success*) break ;;
      failure* | error*) stop "Vercel reported $state for $sha" ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || stop "Vercel still ${state%% *} for $sha after $BUILD_MINUTES min"
    sleep 15
  done
  if git push origin HEAD:main; then
    git push origin --delete "$branch" || echo "::warning::$sha landed, but $branch could not be deleted"
    { echo "sha=$sha"; echo "landed=true"; } >> "$GITHUB_OUTPUT"
    echo "Landed $sha on main."
    exit 0
  fi
  git fetch origin main || stop "fetching main failed"
  if git merge-base --is-ancestor FETCH_HEAD HEAD; then
    stop "main refused $sha although it is a fast-forward (see the push error above)"
  fi
  [ "$round" -lt "$ROUNDS" ] || stop "main moved during each of $ROUNDS builds"
  echo "main moved to $(git rev-parse FETCH_HEAD); rebasing onto it."
  if ! git pull --rebase origin main; then
    git rebase --abort 2>/dev/null || true
    stop "git pull --rebase origin main failed (a conflict, or uncommitted changes)"
  fi
  if [ -n "${AFTER_REBASE:-}" ]; then
    bash -eo pipefail -c "$AFTER_REBASE" || stop "after-rebase failed on the rebased commit"
  fi
done
stop "rounds is $ROUNDS, so nothing was built"
