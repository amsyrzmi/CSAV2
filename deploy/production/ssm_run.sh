#!/bin/bash
# Send a production deploy Systems Manager document to the production instance, wait for the
# command to finish and print its output. The deploy-production workflow runs it from main:
#   ssm_run.sh <plan|apply> <sha> [<migrations>] [<host_digest>]
# Each action has its own document with the action fixed inside it: plan sends csa-production-plan
# with the sha only, apply sends csa-production-apply with the sha, migrations and host digest.
# Needs AWS_REGION and INSTANCE_ID; AWS credentials come from the environment and are never printed.
# Exit: 0 when the command succeeded, 124 when it was still running after the last poll, 64 for a
# refused argument or setting (nothing is sent), 1 for anything else.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C
export AWS_PAGER=""

: "${SSM_POLL_SECONDS:=5}"
: "${SSM_MAX_POLLS:=420}"
# send-command returns before the invocation exists, so the first polls may not find it yet.
MISSING_POLLS=5
# A command the instance has not picked up within this many seconds never runs.
DELIVERY_TIMEOUT=600

die() {
    echo "ssm_run: $1" >&2
    exit 64
}

fail() {
    echo "ssm_run: $1" >&2
    exit 1
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

valid '^[a-z0-9-]+$' "${AWS_REGION:-}" || die "AWS_REGION must be set to the AWS region"
valid '^m?i-[0-9a-f]{8,17}$' "${INSTANCE_ID:-}" || die "INSTANCE_ID must be set to the instance id"
valid '^[0-9]+$' "$SSM_POLL_SECONDS" || die "SSM_POLL_SECONDS must be a whole number of seconds"
valid '^[1-9][0-9]*$' "$SSM_MAX_POLLS" || die "SSM_MAX_POLLS must be a positive whole number"

if [[ "$#" -lt 2 || "$#" -gt 4 ]]; then
    die "usage: ssm_run.sh <plan|apply> <sha> [<migrations>] [<host_digest>]"
fi
action="$1"
sha="$2"
migrations="${3-}"
digest="${4-}"

valid '^(plan|apply)$' "$action" || die "action must be plan or apply"
valid '^[0-9a-f]{40}$' "$sha" || die "sha must be 40 lowercase hex characters"
valid '^([0-9]{4}(,[0-9]{4})*)?$' "$migrations" || die "migrations must be comma-separated four-digit numbers"
# The document's parameters go as one JSON map built by jq, empty values included: no value is
# ever spliced into a command string.
if [[ "$action" == "apply" ]]; then
    valid '^[0-9a-f]{12}$' "$digest" || die "apply needs a 12 character lowercase hex host digest"
    document="csa-production-apply"
    parameters="$(jq -cn --arg sha "$sha" --arg migrations "$migrations" --arg digest "$digest" \
        '{sha: [$sha], migrations: [$migrations], hostDigest: [$digest]}')"
else
    [[ -z "$migrations" ]] || die "plan takes no migrations"
    [[ -z "$digest" ]] || die "plan takes no host digest"
    document="csa-production-plan"
    parameters="$(jq -cn --arg sha "$sha" '{sha: [$sha]}')"
fi

sent="$(aws ssm send-command --region "$AWS_REGION" --document-name "$document" \
    --instance-ids "$INSTANCE_ID" --parameters "$parameters" \
    --timeout-seconds "$DELIVERY_TIMEOUT" --output json)" || fail "send-command failed"
command_id="$(jq -r '.Command.CommandId // empty' <<< "$sent")" ||
    fail "send-command returned unreadable output"
valid '^[0-9a-f-]{36}$' "$command_id" || fail "send-command returned no valid command id"
echo "ssm_run: sent $action for $sha as command $command_id" >&2
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "command_id=$command_id" >> "$GITHUB_OUTPUT"
fi

errors="$(mktemp)"
trap 'rm -f -- "$errors"' EXIT
polls=0
missing=0
status="not yet registered"
while :; do
    if ((polls >= SSM_MAX_POLLS)); then
        echo "ssm_run: command $command_id is still $status after $polls polls; giving up" >&2
        exit 124
    fi
    polls="$((polls + 1))"
    sleep "$SSM_POLL_SECONDS"
    if invocation="$(aws ssm get-command-invocation --region "$AWS_REGION" \
        --command-id "$command_id" --instance-id "$INSTANCE_ID" --output json 2> "$errors")"; then
        status="$(jq -r '.Status // empty' <<< "$invocation")" ||
            fail "get-command-invocation returned unreadable output"
        case "$status" in
            Pending | InProgress | Delayed) continue ;;
        esac
        break
    fi
    if grep -q 'InvocationDoesNotExist' "$errors" && ((missing < MISSING_POLLS)); then
        missing="$((missing + 1))"
        continue
    fi
    cat -- "$errors" >&2
    fail "get-command-invocation failed for command $command_id"
done

jq -j '.StandardOutputContent // ""' <<< "$invocation"
jq -j '.StandardErrorContent // ""' <<< "$invocation" >&2
response="$(jq -r '.ResponseCode // "none"' <<< "$invocation")"
echo "ssm_run: command $command_id finished with status $status (exit code $response)" >&2
[[ "$status" == "Success" ]] || exit 1
