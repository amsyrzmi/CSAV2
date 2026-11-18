#!/bin/bash
# Deploy entry point for both targets, installed root-owned at /usr/local/sbin/csa-prod-deploy
# (production) or /usr/local/sbin/csa-staging-deploy (staging). It is the same file; the installed
# name only picks which config it reads.
#
# AWS Systems Manager runs it as the deploy user with a clean environment:
#   <name> <action> <sha> [<migrations>] [<host_digest>] [<python_digest> <frontend_digest>]
# The two image digests (sha256:<64 hex>) are given together or not at all; on plan and apply they
# are passed on as --python-digest and --frontend-digest, so the rollout needs no registry login.
# Production's apply needs the 12 hex host digest; staging takes none (its apply refuses one).
#
# Every path and the target come from the config file installed next to this script, "<script>.conf":
# a root-owned regular file (not a symlink, not writable by group or others), owned by the uid that
# owns this script. It is parsed line by line and never sourced. Its lines are blank, "#" comments,
# or KEY=value, with exactly these six keys, each required and given once:
#   TARGET       staging or production
#   DEPLOY_USER  the only user allowed to run this script
#   CHECKOUT     the git checkout the rollout runs from
#   PYTHON       the python interpreter of the rollout's environment
#   LOCK         the lock file that stops two deploys overlapping
#   LOG_DIR      where each run's log is written
# Environment variables change nothing here, and nothing in the config lets a safety check weaken.
set -euo pipefail
# Systems Manager starts this in a root-only directory, where git and python would fail to read
# the working directory: leave it before anything else runs.
cd /
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C

self="${BASH_SOURCE[0]}"
name="${self##*/}"

die() {
    echo "$name: $1" >&2
    exit 64
}

# A refused config is a configuration error (78); the message names the key or the reason, and
# never the value.
bad_config() {
    echo "$name: $1" >&2
    exit 78
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

[[ "$self" == /* ]] || bad_config "must be run by its absolute path"
conf="${self}.conf"
[[ -e "$conf" || -L "$conf" ]] || bad_config "no config at $conf"
[[ ! -L "$conf" ]] || bad_config "the config $conf is a symlink"
[[ -f "$conf" ]] || bad_config "the config $conf is not a regular file"
self_owner="$(stat -c %u -- "$self")"
[[ "$(stat -c %u -- "$conf")" == "$self_owner" ]] ||
    bad_config "the config $conf is not owned by the owner of this script"
conf_mode="$(stat -c %a -- "$conf")"
(((8#$conf_mode & 8#022) == 0)) || bad_config "the config $conf is writable by group or others"

declare -A config=()
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
    line_number=$((line_number + 1))
    if [[ -z "${line//[[:space:]]/}" || "$line" == "#"* ]]; then
        continue
    fi
    [[ "$line" =~ ^([A-Z_]+)=(.*)$ ]] || bad_config "the config has a malformed line $line_number"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    case "$key" in
        TARGET) pattern='^(staging|production)$' ;;
        DEPLOY_USER) pattern='^[a-z_][a-z0-9_-]{0,31}$' ;;
        CHECKOUT | PYTHON | LOCK | LOG_DIR) pattern='^/[A-Za-z0-9._/-]+$' ;;
        *) bad_config "the config has an unknown key $key" ;;
    esac
    [[ ! -v config[$key] ]] || bad_config "the config repeats the key $key"
    valid "$pattern" "$value" || bad_config "the config value of $key is malformed"
    if [[ "$pattern" == '^/'* ]]; then
        # No dot segments and no empty ones: the path names exactly one place.
        [[ "$value/" != *"/../"* && "$value/" != *"/./"* && "$value" != *"//"* ]] ||
            bad_config "the config value of $key is malformed"
    fi
    config[$key]="$value"
done < "$conf"
for key in TARGET DEPLOY_USER CHECKOUT PYTHON LOCK LOG_DIR; do
    [[ -v config[$key] ]] || bad_config "the config is missing the key $key"
done
TARGET="${config[TARGET]}"
DEPLOY_USER="${config[DEPLOY_USER]}"
CHECKOUT="${config[CHECKOUT]}"
PYTHON="${config[PYTHON]}"
LOCK="${config[LOCK]}"
LOG_DIR="${config[LOG_DIR]}"

actual_user="$(id -un)"
[[ "$actual_user" == "$DEPLOY_USER" ]] || die "run as $DEPLOY_USER, not $actual_user"

usage="usage: $name <plan|apply> <sha> [<migrations>] [<host_digest>] [<python_digest> <frontend_digest>]"
if [[ $# -eq 5 ]]; then
    die "give both image digests or neither; $usage"
fi
if [[ $# -lt 2 || $# -gt 6 ]]; then
    die "$usage"
fi
action="$1"
sha="$2"
migrations="${3-}"
digest="${4-}"
image_options=()

valid '^(plan|apply)$' "$action" || die "action must be plan or apply"
valid '^[0-9a-f]{40}$' "$sha" || die "sha must be 40 lowercase hex characters"
valid '^([0-9]{4}(,[0-9]{4})*)?$' "$migrations" || die "migrations must be comma-separated four-digit numbers"
host_options=()
if [[ "$TARGET" == "staging" ]]; then
    [[ -z "$digest" ]] || die "staging takes no host digest"
elif [[ "$action" == "apply" ]]; then
    valid '^[0-9a-f]{12}$' "$digest" || die "apply needs a 12 character lowercase hex host digest"
    host_options=(--host-digest "$digest")
else
    [[ -z "$digest" ]] || die "plan takes no host digest"
fi
if [[ $# -eq 6 ]]; then
    for image_digest in "$5" "$6"; do
        valid '^sha256:[0-9a-f]{64}$' "$image_digest" ||
            die "give both image digests or neither, each sha256: and 64 lowercase hex characters"
    done
    image_options=(--python-digest "$5" --frontend-digest "$6")
fi

exec 9>"$LOCK"
flock -n 9 || {
    echo "another deploy is running" >&2
    exit 75
}

log="$LOG_DIR/$(date -u +%Y%m%dT%H%M%SZ)-${action}-${sha:0:7}.log"
# Fail before any git or rollout step when the log cannot be written: tee would report it only
# at the end, turning a finished apply into a failure.
if ! { mkdir -p -m 0750 "$LOG_DIR" && : >> "$log"; } 2> /dev/null; then
    echo "$name: cannot write the log file $log" >&2
    exit 73
fi

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
        "$PYTHON" -m scripts.staging_rollout --target "$TARGET" plan "${image_options[@]}"
    elif [[ -n "$migrations" ]]; then
        "$PYTHON" -m scripts.staging_rollout --target "$TARGET" apply \
            --sha "$sha" --migrations "$migrations" "${host_options[@]}" "${image_options[@]}"
    else
        "$PYTHON" -m scripts.staging_rollout --target "$TARGET" apply \
            --sha "$sha" "${host_options[@]}" "${image_options[@]}"
    fi
}

# fd 3 keeps the refusal on the real stderr; the pipeline below merges stderr into the log.
exec 3>&2
status=0
run < /dev/null 2>&1 | tee -- "$log" || status=$?
exit "$status"
