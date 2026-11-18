#!/bin/bash
# The deploy-staging workflow's two decisions, both for the newest commit only:
#   rollout_gate.sh start     environment: EVENT_NAME (workflow_run or workflow_dispatch), INPUT_SHA
#                             (may be empty or unset), and what resolve_commit.sh needs: GH_TOKEN,
#                             REPOSITORY, IMAGE_NAMESPACE and the optional REGISTRY_USER and
#                             REGISTRY_TOKEN. DEPLOY_TARGET in the environment is ignored.
#   rollout_gate.sh current <sha> <explicit>     environment: GH_TOKEN, REPOSITORY
#
# start runs ../production/resolve_commit.sh from this script's own directory with
# DEPLOY_TARGET=staging and the input sha (empty means the tip of main). On success it prints the
# resolver's five keys (sha, python_digest, frontend_digest, staged_on, explicit) and then
#   proceed=true|false
#   reason=<one line>
# proceed is false only on workflow_run, when the tip already has a staged record, or when the
# resolver says "not ready yet" (exit 3: ci not green, or images not published; that commit's own
# publish run starts the next rollout). A workflow_dispatch always proceeds, an already staged
# commit included, and fails when the commit is not ready. Any other resolver failure prints its
# one-line message on stderr, prints nothing on stdout and exits 1. The reason is built from fixed
# text, the first seven characters of the sha and the resolver's own fixed messages.
#
# current says whether main is still at the commit that was planned: with explicit=true (a sha
# the owner chose) it prints current=true and makes no call; otherwise it reads the tip of main
# and prints current=true or, with a `superseded: ...` line on stderr, current=false. An unreadable
# tip is a failure.
#
# gh's own error text never reaches the output, and GH_TOKEN is read only by gh.
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
resolver="$here/../production/resolve_commit.sh"
sha_message="sha must be the full 40 character lowercase commit SHA"

start() {
    [[ "$#" -eq 0 ]] || die "usage: rollout_gate.sh start"
    local event="${EVENT_NAME:-}" input="${INPUT_SHA:-}" out message line status=0 staged_on=""
    valid '^(workflow_run|workflow_dispatch)$' "$event" \
        || die "EVENT_NAME must be workflow_run or workflow_dispatch"
    if [[ -n "$input" ]]; then
        valid '^[0-9a-f]{40}$' "$input" || die "$sha_message"
    fi

    # Global, not local: the EXIT trap runs after this function has returned.
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    out="$(DEPLOY_TARGET=staging "$resolver" "$input" 2> "$work/stderr")" || status="$?"
    message=""
    IFS= read -r message < "$work/stderr" || true
    [[ -n "$message" ]] || message="the commit could not be resolved"

    if [[ "$status" -eq 3 && "$event" == "workflow_run" ]]; then
        printf 'proceed=false\nreason=nothing to roll out: %s\n' "$message"
        return 0
    fi
    [[ "$status" -eq 0 ]] || die "$message"

    # Exactly the resolver's five keys, once each and in its order, each value checked against its
    # pattern; any other line, repeated key or doubtful value is refused, so nothing else reaches
    # the output.
    local sha="" python_digest="" frontend_digest="" explicit="" key seen=","
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
    printf 'sha=%s\npython_digest=%s\nfrontend_digest=%s\nstaged_on=%s\nexplicit=%s\n' \
        "$sha" "$python_digest" "$frontend_digest" "$staged_on" "$explicit"
    if [[ "$event" == "workflow_run" && -n "$staged_on" ]]; then
        printf 'proceed=false\nreason=%s\n' "main's tip ${sha:0:7} is already staged"
    else
        printf 'proceed=true\nreason=rolling out %s\n' "${sha:0:7}"
    fi
}

current() {
    [[ "$#" -eq 2 ]] || die "usage: rollout_gate.sh current <sha> <explicit>"
    local sha="$1" explicit="$2" tip
    valid '^[0-9a-f]{40}$' "$sha" || die "$sha_message"
    valid '^(true|false)$' "$explicit" || die "explicit must be true or false"
    if [[ "$explicit" == "true" ]]; then
        echo "current=true"
        return 0
    fi
    [[ -n "${GH_TOKEN:+set}" ]] || die "GH_TOKEN must be set"
    [[ -n "${REPOSITORY:-}" ]] && valid '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "$REPOSITORY" \
        || die "REPOSITORY must be set to owner/name"
    tip="$(gh api "repos/$REPOSITORY/commits/main" --jq .sha 2> /dev/null)" \
        || die "could not read the tip of main from GitHub"
    valid '^[0-9a-f]{40}$' "$tip" || die "unexpected answer for the tip of main"
    if [[ "$tip" == "$sha" ]]; then
        echo "current=true"
    else
        echo "current=false"
        echo "superseded: main moved to ${tip:0:7}; the run for the newer commit rolls it out" >&2
    fi
}

command="${1:-}"
[[ "$#" -ge 1 ]] || die "usage: rollout_gate.sh start | current <sha> <explicit>"
shift
case "$command" in
    start) start "$@" ;;
    current) current "$@" ;;
    *) die "usage: rollout_gate.sh start | current <sha> <explicit>" ;;
esac
