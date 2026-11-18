#!/bin/bash
# Write the staged record for a commit, as the deploy-staging workflow's record job does after
# the instance reported success:
#   record_staged.sh <sha>       environment: GH_TOKEN, REPOSITORY (owner/name)
# It POSTs a staging Deployment for the sha described `staging rollout` and a success status
# described `staging rollout finished`, with exactly the bodies scripts/staging_rollout.py's
# record_staged sends (auto_inactive false keeps an older commit's success; required_contexts is
# empty, so no check can hold the deployment back). Both bodies are built with jq. The returned
# deployment id must be a number before it is put in a path. On success it prints
# `Recorded <sha7> as staged`. Any failure of the record itself prints one line on stderr (use
# Re-run failed jobs, since staging is already rolled out) and exits 1; a bad argument or a
# missing credential is a usage error and says so instead. gh's own error text is never shown,
# and GH_TOKEN is read only by gh and never printed.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C

die() {
    echo "$1" >&2
    exit 1
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

# fail: the record was not written.
fail() {
    echo "staging is fine but the staged record was not written; use Re-run failed jobs on this run" >&2
    exit 1
}

[[ "$#" -eq 1 ]] || die "usage: record_staged.sh <sha>"
sha="$1"
valid '^[0-9a-f]{40}$' "$sha" || die "sha must be the full 40 character lowercase commit SHA"
[[ -n "${GH_TOKEN:+set}" ]] || die "GH_TOKEN must be set"
[[ -n "${REPOSITORY:-}" ]] && valid '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "$REPOSITORY" \
    || die "REPOSITORY must be set to owner/name"

# The bodies are the ones record_staged in scripts/staging_rollout.py sends.
deployment_body='{ref: $sha, environment: "staging", auto_merge: false, required_contexts: [], transient_environment: false, production_environment: false, description: "staging rollout"}'
status_body='{state: "success", environment: "staging", auto_inactive: false, description: "staging rollout finished"}'

deployment="$(jq -n --arg sha "$sha" "$deployment_body")" || fail
created="$(gh api --method POST "repos/$REPOSITORY/deployments" --input - 2> /dev/null <<< "$deployment")" \
    || fail
id="$(jq -r '.id | select(type == "number" and . >= 0 and . == floor) | tostring' <<< "$created" 2> /dev/null)" \
    || fail
valid '^[0-9]+$' "$id" || fail

status="$(jq -n "$status_body")" || fail
gh api --method POST "repos/$REPOSITORY/deployments/$id/statuses" --input - \
    > /dev/null 2> /dev/null <<< "$status" || fail

echo "Recorded ${sha:0:7} as staged"
