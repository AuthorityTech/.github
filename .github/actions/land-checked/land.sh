#!/usr/bin/env bash
# Verify the exact land/ commit before fast-forwarding main; rebase and recheck if main moves.
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
  declare -A dispatched_after=()
  while IFS= read -r workflow_file; do
    [ -z "$workflow_file" ] && continue
    dispatched_after["$workflow_file"]=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/runs?head_sha=$sha&event=workflow_dispatch&per_page=100" --jq '.workflow_runs | map(.id) | max // 0') || stop "reading prior $workflow_file runs failed"
    gh workflow run "$workflow_file" --repo "$GITHUB_REPOSITORY" --ref "$branch" || stop "dispatching $workflow_file failed"
  done <<< "${CHECK_WORKFLOWS:-}"
  echo "Check $round of $ROUNDS: waiting up to $BUILD_MINUTES min on $sha ($branch)"
  deadline=$(( $(date +%s) + BUILD_MINUTES * 60 ))
  while :; do
    ruled=$(gh api --paginate "repos/$GITHUB_REPOSITORY/rules/branches/main" --jq '.[] | select(.type == "required_status_checks") | .parameters.required_status_checks[]' | jq -sc 'unique_by(.context, .integration_id)') || stop "reading main requirements failed"
    # Existing sites without a native required context retain their build gate.
    if [ "$ruled" = '[]' ] || [ -z "${CHECK_WORKFLOWS:-}" ]; then
      ruled=$(jq -c '. + [{context:"Vercel"}] | unique_by(.context, .integration_id)' <<< "$ruled")
    fi
    status=$(gh api "repos/$GITHUB_REPOSITORY/commits/$sha/status?per_page=100") || stop "reading statuses failed"
    if [ -n "${CHECK_WORKFLOWS:-}" ]; then
      runs=$(gh api "repos/$GITHUB_REPOSITORY/commits/$sha/check-runs?per_page=100") || stop "reading check runs failed"
    else
      runs='{"total_count":0,"check_runs":[]}'
      status=$(jq --argjson ruled "$ruled" '.statuses |= map(select(.context == "Vercel" or (.context as $name | $ruled | any(.context == $name))))' <<< "$status")
    fi
    (( $(jq '.total_count' <<< "$status") <= 100 && $(jq '.total_count' <<< "$runs") <= 100 )) || stop "partial check read"
    checks=$(jq -nc --argjson s "$status" --argjson c "$runs" '[$s.statuses[] | {name: .context, state, app: null}] + [$c.check_runs[] | {name, state: (.conclusion // "pending"), app: .app.id}]')
    if jq -e 'any(.[]; .state == "failure" or .state == "error" or .state == "cancelled" or .state == "timed_out" or .state == "action_required" or .state == "startup_failure")' <<< "$checks" >/dev/null; then
      stop "a check failed for $sha: $checks"
    fi
    ready=true
    while IFS= read -r workflow_file; do
      [ -z "$workflow_file" ] && continue
      dispatched=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/runs?head_sha=$sha&event=workflow_dispatch&per_page=100" --jq '[.workflow_runs[] | select(.head_sha == "'"$sha"'" and .id > '"${dispatched_after[$workflow_file]}"')] | sort_by(.id) | last | .conclusion // "pending"') || stop "reading dispatched $workflow_file failed"
      case "$dispatched" in
        success) ;;
        pending) ready=false ;;
        *) stop "$workflow_file concluded $dispatched for $sha" ;;
      esac
    done <<< "${CHECK_WORKFLOWS:-}"
    if [ "$ready" = true ] && jq -e --argjson required "$ruled" '
      . as $checks | all(.[]; .state == "success" or .state == "skipped" or .state == "neutral") and
      all($required[]; . as $rule | [$checks[] | select(.name == $rule.context and (($rule.integration_id // 0) <= 0 or .app == $rule.integration_id))] | length > 0 and all(.[]; .state == "success"))
    ' <<< "$checks" >/dev/null; then break; fi
    [ "$(date +%s)" -lt "$deadline" ] || stop "checks still pending for $sha after $BUILD_MINUTES min"
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
