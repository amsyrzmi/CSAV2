#!/bin/bash
# Read a plan's output and print the values apply must send, for $GITHUB_OUTPUT:
#   parse_plan.sh <plan-output-file> <expected-sha>
# prints sha=<40 hex>, migrations=<0486,0487 or empty> and host_digest=<12 hex>. Needs
# DEPLOY_TARGET (staging or production). Only three whole lines are read, each of which
# format_plan prints exactly once: "Target SHA: ", "Pending migrations: " and, on production
# only, "Host settings digest: " (staging has none and prints host_digest= empty). A missing,
# repeated or malformed line, or a plan for another commit, is refused and nothing is printed.
# Both targets also refuse a plan that holds a line starting "Stopped at " or "Missing secret
# files: ": the rollout stopped, or apply would refuse. Staging refuses a downgrade warning too,
# because it never goes back by itself; production accepts it, since the owner's approval decides.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C

die() {
    echo "parse_plan: $1" >&2
    exit 65
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

valid '^(staging|production)$' "${DEPLOY_TARGET:-}" || die "DEPLOY_TARGET must be staging or production"
[[ "$#" -eq 2 ]] || die "usage: parse_plan.sh <plan-output-file> <expected-sha>"
file="$1"
expected="$2"
valid '^[0-9a-f]{40}$' "$expected" || die "the expected sha must be 40 lowercase hex characters"
[[ -f "$file" && -r "$file" ]] || die "cannot read the plan output"

targets=()
pending=()
digests=()
downgrade=0
while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
        "Target SHA: "*) targets+=("${line#Target SHA: }") ;;
        "Pending migrations: "*) pending+=("${line#Pending migrations: }") ;;
        "Host settings digest: "*) digests+=("${line#Host settings digest: }") ;;
        "Stopped at "*) die "the plan has a 'Stopped at' line; the rollout did not finish planning" ;;
        "Missing secret files: "*) die "the plan lists missing secret files; apply would refuse" ;;
        "WARNING: "*"this is a downgrade"*) downgrade=1 ;;
    esac
done < "$file"

[[ "${#targets[@]}" -eq 1 ]] || die "expected one 'Target SHA:' line, found ${#targets[@]}"
[[ "${#pending[@]}" -eq 1 ]] || die "expected one 'Pending migrations:' line, found ${#pending[@]}"
if [[ "$DEPLOY_TARGET" == "production" ]]; then
    [[ "${#digests[@]}" -eq 1 ]] || die "expected one 'Host settings digest:' line, found ${#digests[@]}"
elif [[ "$downgrade" -eq 1 ]]; then
    die "the plan is a downgrade; staging never goes back by itself (roll back by hand, deploy/STAGING_PIPELINE.md)"
fi

sha="${targets[0]}"
valid '^[0-9a-f]{40}$' "$sha" || die "the plan's target sha is malformed"
[[ "$sha" == "$expected" ]] || die "the plan is for $sha, not $expected"

if [[ "${pending[0]}" == "none" ]]; then
    migrations=""
else
    valid '^[0-9]{4}(, [0-9]{4})*$' "${pending[0]}" || die "the plan's pending migrations are malformed"
    migrations="${pending[0]//, /,}"
fi
valid '^([0-9]{4}(,[0-9]{4})*)?$' "$migrations" || die "the plan's pending migrations are malformed"

digest=""
if [[ "$DEPLOY_TARGET" == "production" ]]; then
    digest="${digests[0]}"
    valid '^[0-9a-f]{12}$' "$digest" || die "the plan's host settings digest is malformed"
fi

printf 'sha=%s\nmigrations=%s\nhost_digest=%s\n' "$sha" "$migrations" "$digest"
