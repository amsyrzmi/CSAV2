#!/bin/bash
# The decisions of the deploy-production-auto workflow, which starts a production PLAN by itself
# after staging; the apply still waits for a person's approval on the production environment.
#   auto_plan.sh check            environment: GH_TOKEN, REPOSITORY (owner/name), IMAGE_NAMESPACE and
#                                 the optional REGISTRY_USER and REGISTRY_TOKEN (all passed on to
#                                 resolve_commit.sh). DEPLOY_TARGET in the environment is ignored.
#   auto_plan.sh supersede <sha>  environment: GH_TOKEN, REPOSITORY. <sha> is the tip being planned.
#   auto_plan.sh start            environment: GH_TOKEN, REPOSITORY and the optional
#                                 START_WAIT_SECONDS (default 90) and START_POLL_SECONDS (default 5),
#                                 each a whole number 0 to 999 without a leading zero.
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
#                                                    a run whose head_sha is the tip is requested,
#                                                    queued, in progress, waiting or pending, or
#                                                    concluded success. A run that failed or was
#                                                    cancelled does not count.
# Otherwise proceed=true and reason=planning <sha7>. Any other resolver failure (exit 1), an
# answer that is not exactly the resolver's five keys, or an unreadable run list prints one line
# on stderr, nothing on stdout, and exits 1 (a red run). The reason is built from fixed text, the
# first seven characters of the sha, a run id and the resolver's two known messages. A resolver
# line on stderr is repeated only when it is one line of printable ASCII that does not start with
# `::`; anything else gives "the commit could not be resolved". GH_TOKEN is read only by gh and is
# never printed.
#
# The duplicate guard counts a run by its `head_sha`, which is the tip of main when the run was
# DISPATCHED, not necessarily the commit the run plans (a plan job resolves its commit later). Two
# consequences. A person's rollback with an explicit sha, dispatched while the tip was T and still
# requested, queued, in progress, waiting, pending or succeeded, makes check report `a
# deploy-production run for T already exists`: the plan for T is then started by hand. The other
# direction, a run that planned the tip while its head_sha is an older tip, is not seen by the
# guard; that is fail-safe, because supersede cancels such a run while it waits and the new plan
# starts.
#
# supersede <sha> prints one line per waiting deploy-production run and cancels a run ONLY when
# every condition holds, each read from the API with fail-closed validation before any comparison
# (a null or missing field, or a value of the wrong shape, leaves the run alone: in jq `null != $x`
# is true):
#   the run is `waiting`; its event is `workflow_dispatch`; its triggering actor is $AUTO_ACTOR; its
#   branch is `main`; its head_sha is not <sha>; its jobs are exactly `plan` (completed, success)
#   and `apply` (waiting), so no other job runs.
# The run and its jobs are read again immediately before the cancel (`run <id> moved on; left
# alone` when anything changed); the cancel is one POST, which answers 202 or 409; a refusal is read
# once more and never retried. It never approves, rejects or touches pending_deployments. The
# window of one API round trip between the last read and the cancel remains: an approval can land
# first, and a cancel does not stop an SSM command that was already sent. Lines:
#   cancelled run <id> (planned <sha7>)
#   left alone: run <id> (<reason>)    reason: started by <login>, same commit, apply is not
#                                      waiting, not a dispatch, not on main, unreadable fields
#   run <id> moved on; left alone
#   warning: could not cancel run <id>; it stays waiting
# It exits 0 unless the listing or a read fails. It runs once, before start.
#
# start dispatches deploy-production.yml on main with NO input (an empty sha means the tip of main,
# so the auto path can never ask for a rollback), then reads the run list again, up to
# START_WAIT_SECONDS / START_POLL_SECONDS times, rounded up (with START_POLL_SECONDS=0 it reads
# START_WAIT_SECONDS times without sleeping; the first read after the dispatch is immediate), until
# a run that was not in the first list appears, and prints `started deploy-production run <id>:
# <url>`. When none appears it fails (exit 1) with "the dispatch was accepted but no
# deploy-production run started (check that workflow_run chaining is not limited); look for a run
# before starting another" (a run can be slow to appear). A refused dispatch exits 1 with gh's
# first error line.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C

# The login GitHub shows as the triggering actor of a dispatch made with the workflow's own token.
# supersede cancels only runs started by this login; if it never matches, nothing is cancelled.
AUTO_ACTOR="github-actions[bot]"

die() {
    echo "$1" >&2
    exit 1
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

# plain <text> <fallback>: the text when it is one line of printable ASCII that cannot start a
# workflow command, otherwise the fallback. Used for every line of another program that is echoed.
plain() {
    if valid '^[[:print:]]{1,300}$' "$1" && [[ "$1" != ::* ]]; then
        printf '%s' "$1"
    else
        printf '%s' "$2"
    fi
}

# CDPATH= keeps a CDPATH in the environment from redirecting the cd (and printing the directory).
here="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" > /dev/null && pwd)"
resolver="$here/resolve_commit.sh"
usage="usage: auto_plan.sh check | supersede <sha> | start"
runs_path="actions/workflows/deploy-production.yml/runs"

need_environment() {
    [[ -n "${GH_TOKEN:+set}" ]] || die "GH_TOKEN must be set"
    [[ -n "${REPOSITORY:-}" ]] && valid '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "$REPOSITORY" \
        || die "REPOSITORY must be set to owner/name"
}

check() {
    [[ "$#" -eq 0 ]] || die "$usage"
    need_environment

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
        die "$(plain "$message" "the commit could not be resolved")"
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
    runs="$(gh api "repos/$REPOSITORY/$runs_path?head_sha=$sha&per_page=100" \
        2> /dev/null)" || die "could not read the deploy-production runs from GitHub"
    [[ -n "${runs//[[:space:]]/}" ]] || die "unexpected answer for the deploy-production runs"
    # One line, so the repository's line-based quote check can read it.
    blocking='if (.workflow_runs | type) == "array" then [.workflow_runs[] | select(.head_sha == $sha and (.status == "queued" or .status == "requested" or .status == "in_progress" or .status == "waiting" or .status == "pending" or .conclusion == "success")) | .id | tostring] | first // "" else error("shape") end'
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

# --- supersede ---------------------------------------------------------------------------------

# The jq programs are single lines so the repository's line-based quote check can read this file.
# `row` turns a run into "<id> <status> <event> <branch> <sha> <login>" when EVERY field is present
# and well-formed (strings that match, no whitespace), and into "<id> unreadable" otherwise, so a
# null, a missing field or a field of the wrong type never reaches a comparison. \A and \z anchor
# the whole value (a `$` would also match before a trailing newline).
row_definition='def row: if (.id | type) == "number" and (.id | floor) == .id and .id > 0 then (.id | tostring) as $id | .head_sha as $sha | .status as $st | .event as $ev | .head_branch as $br | (.triggering_actor.login? // null) as $lg | if ($sha | type) == "string" and ($sha | test("\\A[0-9a-f]{40}\\z")) and ($st | type) == "string" and ($st | test("\\A[a-z_]{1,30}\\z")) and ($ev | type) == "string" and ($ev | test("\\A[a-z_]{1,40}\\z")) and ($br | type) == "string" and ($br | test("\\A[A-Za-z0-9._/-]{1,255}\\z")) and ($lg | type) == "string" and ($lg | test("\\A[A-Za-z0-9_.\\[\\]-]{1,100}\\z")) then "\($id) \($st) \($ev) \($br) \($sha) \($lg)" else "\($id) unreadable" end else error("id") end;'
list_program='if (.workflow_runs | type) == "array" then .workflow_runs[] | row else error("shape") end'
# The jobs of a run: "waiting" when they are exactly `plan` (completed, success) and `apply`
# (waiting, no conclusion yet), "other" for any other well-formed list, "unreadable" when the
# shape is not as expected.
jobs_program='if (.jobs | type) == "array" and all(.jobs[]; type == "object" and (.name | type) == "string" and (.status | type) == "string" and has("conclusion") and ((.conclusion | type) == "string" or .conclusion == null)) then if (.jobs | map(.name) | sort) == ["apply", "plan"] and (.jobs | map(select(.name == "plan"))[0] | .status == "completed" and .conclusion == "success") and (.jobs | map(select(.name == "apply"))[0] | .status == "waiting" and .conclusion == null) then "waiting" else "other" end else "unreadable" end'

# judge <id> <status> <event> <branch> <sha> <login>: sets verdict to "cancel" only when every
# condition for a cancel holds, otherwise to the reason the run is left alone. The tip being
# planned is $tip.
judge() {
    local id="$1" status="$2" event="$3" branch="$4" sha="$5" login="$6" jobs state
    verdict="unreadable fields"
    [[ "$status" != unreadable ]] || return 0
    if [[ "$event" != workflow_dispatch ]]; then
        verdict="not a dispatch"
    elif [[ "$branch" != main ]]; then
        verdict="not on main"
    elif [[ "$login" != "$AUTO_ACTOR" ]]; then
        verdict="started by $login"
    elif [[ "$sha" == "$tip" ]]; then
        verdict="same commit"
    elif [[ "$status" != waiting ]]; then
        verdict="apply is not waiting"
    else
        jobs="$(gh api "repos/$REPOSITORY/actions/runs/$id/jobs" 2> /dev/null < /dev/null)" \
            || die "could not read the jobs of run $id from GitHub"
        state="$(jq -r "$jobs_program" <<< "$jobs" 2> /dev/null)" || state=unreadable
        case "$state" in
            waiting) verdict=cancel ;;
            other) verdict="apply is not waiting" ;;
            *) verdict="unreadable fields" ;;
        esac
    fi
}

# fresh <id>: reads the run again (run and jobs) and judges it as above.
fresh() {
    local raw row id status event branch sha login
    raw="$(gh api "repos/$REPOSITORY/actions/runs/$1" 2> /dev/null < /dev/null)" \
        || die "could not read run $1 from GitHub"
    row="$(jq -r "$row_definition row" <<< "$raw" 2> /dev/null)" || row="$1 unreadable"
    IFS=' ' read -r id status event branch sha login <<< "$row"
    if [[ "$id" != "$1" ]]; then
        verdict="unreadable fields"
        return 0
    fi
    judge "$id" "$status" "$event" "$branch" "$sha" "$login"
}

supersede() {
    [[ "$#" -eq 1 ]] || die "$usage"
    tip="$1"
    valid '^[0-9a-f]{40}$' "$tip" || die "the sha must be 40 lowercase hexadecimal characters"
    need_environment

    local runs rows id status event branch sha login
    runs="$(gh api "repos/$REPOSITORY/$runs_path?status=waiting&per_page=100" \
        2> /dev/null)" || die "could not read the deploy-production runs from GitHub"
    [[ -n "${runs//[[:space:]]/}" ]] || die "unexpected answer for the deploy-production runs"
    rows="$(jq -r "$row_definition $list_program" <<< "$runs" 2> /dev/null)" \
        || die "unexpected answer for the deploy-production runs"

    while IFS=' ' read -r id status event branch sha login; do
        [[ -n "$id" ]] || continue
        valid '^[0-9]+$' "$id" || die "unexpected answer for the deploy-production runs"
        judge "$id" "$status" "$event" "$branch" "$sha" "$login"
        if [[ "$verdict" != cancel ]]; then
            echo "left alone: run $id ($verdict)"
            continue
        fi
        # The listing is a moment old: read the run again just before the one cancel call.
        fresh "$id"
        if [[ "$verdict" != cancel ]]; then
            echo "run $id moved on; left alone"
            continue
        fi
        if gh api --method POST "repos/$REPOSITORY/actions/runs/$id/cancel" \
            > /dev/null 2>&1 < /dev/null; then
            echo "cancelled run $id (planned ${sha:0:7})"
        else
            # 409 or a refusal: read once more, never retry.
            fresh "$id"
            if [[ "$verdict" == cancel ]]; then
                echo "warning: could not cancel run $id; it stays waiting"
            else
                echo "run $id moved on; left alone"
            fi
        fi
    done <<< "$rows"
}

# --- start -------------------------------------------------------------------------------------

# "<id> <html_url or ->" for each run, newest first; the url is "-" unless it has the exact shape.
ids_program='if (.workflow_runs | type) == "array" then .workflow_runs[] | if (.id | type) == "number" and (.id | floor) == .id and .id > 0 then "\(.id | tostring) \(.html_url | if type == "string" and test("\\Ahttps://github\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+\\z") then . else "-" end)" else error("id") end else error("shape") end'

# list_runs <message when gh fails>: the dispatch runs on main, as "<id> <url or ->" lines.
list_runs() {
    local raw query="repos/$REPOSITORY/$runs_path?event=workflow_dispatch&branch=main&per_page=30"
    raw="$(gh api "$query" 2> /dev/null < /dev/null)" || die "$1"
    [[ -n "${raw//[[:space:]]/}" ]] || die "unexpected answer for the deploy-production runs"
    jq -r "$ids_program" <<< "$raw" 2> /dev/null \
        || die "unexpected answer for the deploy-production runs"
}

start() {
    [[ "$#" -eq 0 ]] || die "$usage"
    need_environment
    local wait="${START_WAIT_SECONDS:-90}" poll="${START_POLL_SECONDS:-5}"
    valid '^(0|[1-9][0-9]{0,2})$' "$wait" \
        || die "START_WAIT_SECONDS must be a whole number of seconds, 0 to 999"
    valid '^(0|[1-9][0-9]{0,2})$' "$poll" \
        || die "START_POLL_SECONDS must be a whole number of seconds, 0 to 999"

    # The runs that exist before the dispatch are not the new one.
    local before="" id url rows message attempts i
    rows="$(list_runs "could not read the deploy-production runs from GitHub")" || exit 1
    while IFS=' ' read -r id url; do
        [[ -z "$id" ]] || before+=" $id"
    done <<< "$rows"
    before+=" "

    # No -f and no sha: an empty sha means the tip of main, never a rollback.
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    if ! gh workflow run deploy-production.yml --repo "$REPOSITORY" --ref main \
        > /dev/null 2> "$work/dispatch" < /dev/null; then
        message=""
        IFS= read -r message < "$work/dispatch" || true
        die "$(plain "$message" "the dispatch was refused")"
    fi

    if [[ "$poll" -gt 0 ]]; then
        # Rounded up, so a wait shorter than one poll still reads once more after that poll.
        attempts=$(((wait + poll - 1) / poll))
    else
        attempts="$wait"
    fi
    for ((i = 0; i <= attempts; i++)); do
        if [[ "$i" -gt 0 && "$poll" -gt 0 ]]; then
            sleep "$poll"
        fi
        rows="$(list_runs "could not read the deploy-production runs from GitHub; the dispatch was already sent, look for a run before starting another")" || exit 1
        while IFS=' ' read -r id url; do
            [[ -n "$id" && "$before" != *" $id "* ]] || continue
            valid '^[0-9]+$' "$id" && [[ "$url" == */actions/runs/"$id" && "$url" != - ]] \
                || die "unexpected answer for the deploy-production runs"
            echo "started deploy-production run $id: $url"
            return 0
        done <<< "$rows"
    done
    die "the dispatch was accepted but no deploy-production run started (check that workflow_run chaining is not limited); look for a run before starting another"
}

command="${1:-}"
[[ "$#" -ge 1 ]] || die "$usage"
shift
case "$command" in
    check) check "$@" ;;
    supersede) supersede "$@" ;;
    start) start "$@" ;;
    *) die "$usage" ;;
esac
