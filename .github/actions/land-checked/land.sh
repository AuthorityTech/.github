#!/usr/bin/env bash
# Verify the exact land/ commit before fast-forwarding main; rebase and recheck if main moves.
set -euo pipefail

workflow=$(basename "${GITHUB_WORKFLOW_REF%@*}")
branch="land/${workflow%.*}/$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
retry_branch="${branch}-retry"
used_retry=false

stop() {
  [ "$used_retry" = true ] && git push origin --delete "$retry_branch" 2>/dev/null || true
  echo "::error::$1; main untouched, $branch kept"
  exit 1
}

for round in $(seq "$ROUNDS"); do
  sha=$(git rev-parse HEAD)
  git push --force origin "HEAD:refs/heads/$branch" || stop "pushing $branch failed"
  declare -A dispatched_after=() reported=()
  while IFS= read -r workflow_file; do
    [ -z "$workflow_file" ] && continue
    dispatched_after["$workflow_file"]=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/runs?head_sha=$sha&event=workflow_dispatch&per_page=100" --jq '.workflow_runs | map(.id) | max // 0') || stop "reading prior $workflow_file runs failed"
    gh workflow run "$workflow_file" --repo "$GITHUB_REPOSITORY" --ref "$branch" || stop "dispatching $workflow_file failed"
  done <<< "${CHECK_WORKFLOWS:-}"
  echo "Check $round of $ROUNDS: waiting up to $BUILD_MINUTES min on $sha ($branch)"
  started=$(date +%s)
  deadline=$(( started + BUILD_MINUTES * 60 ))
  repush_at=$(( started + 60 ))
  repushed=false
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
    # GitHub counts no workflow_dispatch check run toward a ruleset, and a GITHUB_TOKEN push to land/ starts no
    # other workflow, so a GitHub Actions check run here never satisfies main (counts: false). Its passed job is
    # reported below as a commit status, which GitHub does count; that status is this token's, the GitHub Actions app.
    reported_json=$(printf '%s\n' "${!reported[@]}" | jq -Rsc 'split("\n") | map(select(. != ""))')
    checks=$(jq -nc --argjson s "$status" --argjson c "$runs" --argjson mine "$reported_json" '[$s.statuses[] | {name: .context, state, app: (if .context as $n | $mine | index($n) then 15368 else null end), counts: true}] + [$c.check_runs[] | {name, state: (.conclusion // "pending"), app: .app.id, counts: (.app.id != 15368)}]')
    if jq -e 'any(.[]; .state == "failure" or .state == "error" or .state == "cancelled" or .state == "timed_out" or .state == "action_required" or .state == "startup_failure")' <<< "$checks" >/dev/null; then
      stop "a check failed for $sha: $checks"
    fi
    ready=true
    running=false
    while IFS= read -r workflow_file; do
      [ -z "$workflow_file" ] && continue
      latest=$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow_file/runs?head_sha=$sha&event=workflow_dispatch&per_page=100" --jq '[.workflow_runs[] | select(.head_sha == "'"$sha"'" and .id > '"${dispatched_after[$workflow_file]}"')] | sort_by(.id) | last | "\(.id // 0) \(.conclusion // "pending") \(.status // "queued")"') || stop "reading dispatched $workflow_file failed"
      read -r run_id dispatched run_status <<< "$latest"
      case "$dispatched" in
        success)
          jobs=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$run_id/jobs?per_page=100" --jq '.jobs[] | select(.conclusion == "success") | "\(.name)\t\(.html_url)"') || stop "reading $workflow_file jobs failed"
          while IFS=$'\t' read -r job url; do
            [ -z "$job" ] || [ -n "${reported[$job]:-}" ] && continue
            jq -e --arg n "$job" 'any(.[]; .context == $n)' <<< "$ruled" >/dev/null || continue
            gh api -X POST "repos/$GITHUB_REPOSITORY/statuses/$sha" -f state=success -f context="$job" -f description="$workflow_file passed on this commit" -f target_url="$url" </dev/null >/dev/null || stop "reporting $job as a commit status failed (the caller needs statuses: write)"
            reported[$job]=1
            ready=false
          done <<< "$jobs"
          ;;
        pending)
          ready=false
          [ "$run_status" = in_progress ] && running=true
          ;;
        *) stop "$workflow_file concluded $dispatched for $sha" ;;
      esac
    done <<< "${CHECK_WORKFLOWS:-}"
    if [ "$ready" = true ] && jq -e --argjson required "$ruled" '
      . as $checks | all(.[]; .state == "success" or .state == "skipped" or .state == "neutral") and
      all($required[]; . as $rule | [$checks[] | select(.counts and .name == $rule.context and (($rule.integration_id // 0) <= 0 or .app == $rule.integration_id))] | length > 0 and all(.[]; .state == "success"))
    ' <<< "$checks" >/dev/null; then break; fi
    now=$(date +%s)
    # A dispatched run that is running ends by its own timeout-minutes, so build-minutes never cuts one off:
    # MR 2026-11-25, run 45568848156, held passed findings when Build tests grew past 15 min (#873).
    [ "$now" -lt "$deadline" ] || [ "$running" = true ] || stop "checks still pending for $sha after $BUILD_MINUTES min"
    # GitHub occasionally drops the push webhook to Vercel (~1%); Vercel posts pending within seconds when it arrives.
    if [ "$repushed" = false ] && [ "$now" -ge "$repush_at" ] && jq -e 'any(.[]; .context == "Vercel")' <<< "$ruled" >/dev/null && ! jq -e 'any(.[]; .name == "Vercel")' <<< "$checks" >/dev/null; then
      repushed=true
      echo "No Vercel status for $sha after 60s; pushing $retry_branch once to re-trigger Vercel."
      git push --force origin "HEAD:refs/heads/$retry_branch" || stop "pushing $retry_branch failed"
      used_retry=true
    fi
    sleep 15
  done
  if git push origin HEAD:main; then
    git push origin --delete "$branch" || echo "::warning::$sha landed, but $branch could not be deleted"
    if [ "$used_retry" = true ]; then
      git push origin --delete "$retry_branch" || echo "::warning::$sha landed, but $retry_branch could not be deleted"
    fi
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
