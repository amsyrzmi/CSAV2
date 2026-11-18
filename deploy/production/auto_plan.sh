#!/bin/bash
# The decisions of the deploy-production-auto workflow, which starts a production PLAN by itself
# after staging; the apply still waits for a person's approval on the production environment.
#   auto_plan.sh check      environment: GH_TOKEN, REPOSITORY (owner/name), IMAGE_NAMESPACE and the
#                           optional REGISTRY_USER and REGISTRY_TOKEN (all passed on to
#                           resolve_commit.sh). DEPLOY_TARGET in the environment is ignored.
#
# check asks whether there is anything to plan for the tip of main (never for any other commit: the
# sha GitHub attaches to the triggering run is not used). It runs resolve_commit.sh from this
# script's own directory with DEPLOY_TARGET=staging and no sha: that mode never refuses at the
# staged check, it reports `staged_on` (empty when the tip has no staged record), and it exits 3
# for "not ready yet" (the ci run has not succeeded, or the images are not published). It prints,
# for $GITHUB_OUTPUT,
#   sha=<40 hex>            (when the resolver answered)
#   proceed=true|false
#   reason=<one line>
# and ends green (exit 0) for every case in which there is nothing to plan:
#   nothing to plan: <message>                       resolver exit 3. <message> is the resolver's
#                                                    own line only when it is one of its two known
#                                                    not-ready lines; any other text gives
#                                                    "the commit is not ready yet".
#   nothing to plan: main's tip <sha7> is not staged yet     staged_on is empty (paused, superseded
#                                                    or the record job failed)
#   a deploy-production run for <sha7> already exists: <run id>
#                                                    a run whose head_sha is the tip is queued,
#                                                    in progress, waiting or pending, or concluded
#                                                    success. A run that failed or was cancelled
#                                                    does not count.
# Otherwise proceed=true and reason=planning <sha7>. Any other resolver failure (exit 1), an
# answer that is not exactly the resolver's five keys, or an unreadable run list prints one line
# on stderr, nothing on stdout, and exits 1 (a red run). The reason is built from fixed text, the
# first seven characters of the sha, a run id and the resolver's two known messages. GH_TOKEN is
# read only by gh and is never printed.
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

# CDPATH= keeps a CDPATH in the environment from redirecting the cd (and printing the directory).
here="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" > /dev/null && pwd)"
resolver="$here/resolve_commit.sh"
usage="usage: auto_plan.sh check"

check() {
    [[ "$#" -eq 0 ]] || die "$usage"
    [[ -n "${GH_TOKEN:+set}" ]] || die "GH_TOKEN must be set"
    [[ -n "${REPOSITORY:-}" ]] && valid '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "$REPOSITORY" \
        || die "REPOSITORY must be set to owner/name"

    # Global, not local: the EXIT trap runs after this function has returned.
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    local out message status=0 line
    out="$(DEPLOY_TARGET=staging "$resolver" "" 2> "$work/stderr")" || status="$?"
    message=""
    IFS= read -r message < "$work/stderr" || true

    if [[ "$status" -eq 3 ]]; then
        # Only the resolver's two known not-ready lines are repeated; anything else is replaced,
        # so no other text reaches the output.
        if ! valid '^(the ci run for [0-9a-f]{7} has not succeeded on main|images for [0-9a-f]{7} are not published \(csa-(python|frontend)\))$' "$message"; then
            message="the commit is not ready yet"
        fi
        printf 'proceed=false\nreason=nothing to plan: %s\n' "$message"
        return 0
    fi
    if [[ "$status" -ne 0 ]]; then
        die "${message:-the commit could not be resolved}"
    fi

    # Exactly the resolver's five keys, once each and in its order, each value checked against its
    # pattern; any other line, repeated key or doubtful value is refused.
    local sha="" python_digest="" frontend_digest="" staged_on="" explicit="" key seen=","
    local digest='^sha256:[0-9a-f]{64}$'
    while IFS= read -r line; do
        [[ "$line" == *=* ]] || die "unexpected answer from the commit resolver"
        key="${line%%=*}"
        seen+="$key,"
        case "$key" in
            sha) sha="${line#*=}" ;;
            python_digest) python_digest="${line#*=}" ;;
            frontend_digest) frontend_digest="${line#*=}" ;;
            staged_on) staged_on="${line#*=}" ;;
            explicit) explicit="${line#*=}" ;;
            *) die "unexpected answer from the commit resolver" ;;
        esac
    done <<< "$out"
    [[ "$seen" == ",sha,python_digest,frontend_digest,staged_on,explicit," ]] \
        || die "unexpected answer from the commit resolver"
    valid '^[0-9a-f]{40}$' "$sha" \
        && valid "$digest" "$python_digest" \
        && valid "$digest" "$frontend_digest" \
        && { [[ -z "$staged_on" ]] \
            || valid '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$staged_on"; } \
        && valid '^(true|false)$' "$explicit" \
        || die "unexpected answer from the commit resolver"
    local sha7="${sha:0:7}"

    if [[ -z "$staged_on" ]]; then
        printf 'sha=%s\nproceed=false\nreason=%s\n' "$sha" \
            "nothing to plan: main's tip $sha7 is not staged yet"
        return 0
    fi

    # A run for this commit that is already going, or already done well, blocks a second plan.
    local runs existing
    runs="$(gh api "repos/$REPOSITORY/actions/workflows/deploy-production.yml/runs?head_sha=$sha&per_page=100" \
        2> /dev/null)" || die "could not read the deploy-production runs from GitHub"
    [[ -n "${runs//[[:space:]]/}" ]] || die "unexpected answer for the deploy-production runs"
    # One line, so the repository's line-based quote check can read it.
    blocking='if (.workflow_runs | type) == "array" then [.workflow_runs[] | select(.head_sha == $sha and (.status == "queued" or .status == "in_progress" or .status == "waiting" or .status == "pending" or .conclusion == "success")) | .id | tostring] | first // "" else error("shape") end'
    existing="$(jq -r --arg sha "$sha" "$blocking" <<< "$runs" 2> /dev/null)" \
        || die "unexpected answer for the deploy-production runs"
    if [[ -n "$existing" ]]; then
        valid '^[0-9]+$' "$existing" || die "unexpected answer for the deploy-production runs"
        printf 'sha=%s\nproceed=false\nreason=a deploy-production run for %s already exists: %s\n' \
            "$sha" "$sha7" "$existing"
        return 0
    fi
    printf 'sha=%s\nproceed=true\nreason=planning %s\n' "$sha" "$sha7"
}

command="${1:-}"
[[ "$#" -ge 1 ]] || die "$usage"
shift
case "$command" in
    check) check "$@" ;;
    *) die "$usage" ;;
esac
