#!/bin/bash
# Production deploy entry point, installed root-owned at /usr/local/sbin/csa-prod-deploy.
#
# AWS Systems Manager runs it as the deploy user with a clean environment:
#   deploy_entry.sh <action> <sha> [<migrations>] [<host_digest>]
# The four variables below are defaults only; nothing here lets them weaken a safety check.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C

: "${CHECKOUT:=/home/ubuntu/CSAV2}"
: "${PYTHON:=/opt/csa-deploy/venv/bin/python}"
: "${LOCK:=/var/lock/csa-prod-deploy.lock}"
: "${LOG_DIR:=/var/log/csa-prod-deploy}"

die() {
    echo "csa-prod-deploy: $1" >&2
    exit 64
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

if [[ $# -lt 2 || $# -gt 4 ]]; then
    die "usage: csa-prod-deploy <plan|apply> <sha> [<migrations>] [<host_digest>]"
fi
action="$1"
sha="$2"
migrations="${3-}"
digest="${4-}"

valid '^(plan|apply)$' "$action" || die "action must be plan or apply"
valid '^[0-9a-f]{40}$' "$sha" || die "sha must be 40 lowercase hex characters"
valid '^([0-9]{4}(,[0-9]{4})*)?$' "$migrations" || die "migrations must be comma-separated four-digit numbers"
if [[ "$action" == "apply" ]]; then
    valid '^[0-9a-f]{12}$' "$digest" || die "apply needs a 12 character lowercase hex host digest"
else
    [[ -z "$digest" ]] || die "plan takes no host digest"
fi

exec 9>"$LOCK"
flock -n 9 || {
    echo "another deploy is running" >&2
    exit 75
}

mkdir -p -m 0750 "$LOG_DIR"
log="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-${action}-${sha:0:7}.log"

# Each step returns on failure explicitly: errexit is suspended inside a pipeline's `||` list.
run() {
    git -C "$CHECKOUT" fetch origin main || return
    # The commit's own copy of the rollout code would run next, so it must already be on main.
    git -C "$CHECKOUT" merge-base --is-ancestor "$sha" origin/main || {
        echo "commit is not on main"
        echo "commit is not on main" >&3
        return 1
    }
    git -C "$CHECKOUT" checkout --detach "$sha" || return
    cd "$CHECKOUT" || return
    if [[ "$action" == "plan" ]]; then
        "$PYTHON" -m scripts.staging_rollout --target production plan
    elif [[ -n "$migrations" ]]; then
        "$PYTHON" -m scripts.staging_rollout --target production apply \
            --sha "$sha" --migrations "$migrations" --host-digest "$digest"
    else
        "$PYTHON" -m scripts.staging_rollout --target production apply \
            --sha "$sha" --host-digest "$digest"
    fi
}

# fd 3 keeps the refusal on the real stderr; the pipeline below merges stderr into the log.
exec 3>&2
status=0
run < /dev/null 2>&1 | tee -- "$log" || status=$?
exit "$status"
