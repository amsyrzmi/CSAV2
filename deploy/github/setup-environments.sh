#!/bin/bash
# Check or apply the GitHub side of a deploy target: the environments the deploy workflows run in,
# their branch policies and reviewers, the environment secrets and the repository variables. Run it
# on a machine where `gh` is logged in as an administrator of the repository:
#
#   setup-environments.sh <staging|production> [--check] --repo <owner/name>
#       [--region <region>] [--image-namespace <ghcr.io/owner>] [--reviewer <login>]...
#       [--replace-reviewers] [--auto-plan true|false] [--paused true|false]
#
#   --check             read-only (only `gh api` GET calls). Prints one line per item,
#                       `[n/total] <what>: <result>` where the result is `ok`, `missing` or
#                       `differs` (the two switches add their value: `ok (true)`, `ok (unset: off)`,
#                       `differs (<value>)`, `differs (not true or false)`), `note:` lines for
#                       what is only reported, then `check: everything is ok` (exit 0) or
#                       `check: <n> item(s) not ok` (exit 1). Secret and variable values are never
#                       printed; the two switches (PROD_AUTO_PLAN, STAGING_AUTO_PAUSED) print
#                       `true`, `false` or `unset: off`.
#   --region, --image-namespace
#                       the repository variables of the target; check compares them, apply sets
#                       them (a required variable that is absent and not given is reported missing).
#   --reviewer          production only (at most six): the people who approve a production apply.
#                       Check compares them with the live set by login.
#   --replace-reviewers production only, apply only: allows the reviewer set of an existing
#                       `production` to change (without it apply prints the old and the new
#                       logins and stops before the first write).
#   --auto-plan         production only: the PROD_AUTO_PLAN switch (apply sets it only when given).
#   --paused            staging only: the STAGING_AUTO_PAUSED switch (apply sets it only when given).
#
# Without --check the script applies. For each environment of the target it reads the live state,
# builds the complete desired state (reviewers, wait timer, self-review, admin bypass and the
# branch-policy flags, so a PUT can never drop one), writes only when something differs, re-reads
# the environment after every write and stops when a field is not what was intended, then makes
# the `main`-only branch policy. Then it sets the secrets and the repository variables. A secret
# value comes from the environment variable SETUP_<NAME>, or from a hidden prompt when stdin and
# stderr are a terminal (Enter skips one), and reaches `gh secret set` on stdin only: never an
# argument, never printed, never logged. A secret that already exists is not prompted for and not
# overwritten unless its SETUP_<NAME> is set (an explicit rotation). A secret or required variable
# that has no value ends the run with `apply: <n> item(s) still missing` (exit 1), after
# everything else was applied. Every input is validated before the first `gh` call, and every
# secret value before the first write. The one permission check is `gh api repos/<repo>`
# (`.permissions.admin`): the login check of gh itself exits non-zero when any other configured
# account is stale. No input is run through eval.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C

# ---- patterns ---------------------------------------------------------------------------------
RE_REPO='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
RE_REGION='^[a-z]{2}(-[a-z]+)+-[0-9]$'
# The same pattern as deploy/production/resolve_commit.sh.
RE_NAMESPACE='^ghcr\.io/[a-z0-9][a-z0-9-]*$'
RE_REVIEWER='^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$'
# The three kinds of secret value (the script prints the kind, never the value).
RE_ROLE_ARN='^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,._@-]+$'
RE_INSTANCE_ID='^mi-[0-9a-f]{17}$'
RE_ORIGIN='^https://[A-Za-z0-9.-]+$'
MAX_REVIEWERS=6

# ---- the two targets --------------------------------------------------------------------------
# ENVIRONMENTS: `name|reviewers|admin bypass`, in the order they are processed. Reviewers are
# `required` (production: at least one, and the given set when --reviewer is given) or `preserved`
# (never judged, only noted). Admin bypass is `false` (the owner's decision for production and
# production-plan) or `carry` (staging and staging-images keep what they have).
# SECRETS: `environment|name`, the environment secrets each workflow reads.
# VARIABLES: `name|kind|required`, the repository variables. The kind says which flag gives the
# value (namespace: --image-namespace, region: --region, switch: --auto-plan or --paused). A switch
# is never required: unset is a valid state (off).
configure_target() {
    case "$1" in
        staging)
            ENVIRONMENTS=(
                "staging|preserved|carry"
                "staging-images|preserved|carry"
            )
            SECRETS=(
                "staging|STAGING_DEPLOY_ROLE_ARN"
                "staging|STAGING_INSTANCE_ID"
            )
            VARIABLES=(
                "IMAGE_NAMESPACE|namespace|required"
                "STAGING_DEPLOY_REGION|region|required"
                "STAGING_AUTO_PAUSED|switch|optional"
            )
            ;;
        production)
            ENVIRONMENTS=(
                "production-plan|preserved|false"
                "production|required|false"
            )
            SECRETS=(
                "production-plan|PROD_PLAN_ROLE_ARN"
                "production-plan|PROD_INSTANCE_ID"
                "production|PROD_APPLY_ROLE_ARN"
                "production|PROD_INSTANCE_ID"
                "production|PROD_CONSOLE_ORIGIN"
            )
            VARIABLES=(
                "IMAGE_NAMESPACE|namespace|required"
                "PROD_DEPLOY_REGION|region|required"
                "PROD_AUTO_PLAN|switch|optional"
            )
            ;;
        *) return 1 ;;
    esac
}

usage() {
    echo "usage: setup-environments.sh <staging|production> [--check] --repo <owner/name> [--region <region>]" >&2
    echo "       [--image-namespace <ghcr.io/owner>] [--reviewer <login>]... [--replace-reviewers]" >&2
    echo "       [--auto-plan true|false] [--paused true|false]" >&2
    exit 2
}

# bad <reason>: a usage error that never contains the rejected value.
bad() {
    echo "$1" >&2
    exit 2
}

die() {
    echo "$1" >&2
    exit 1
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

# ---- arguments, validated before any gh call --------------------------------------------------
[[ "$#" -ge 1 ]] || usage
TARGET="$1"
shift
configure_target "$TARGET" || usage

SEEN=" "
once() {
    [[ "$SEEN" != *" $1 "* ]] || bad "$1 may be given only once"
    SEEN="$SEEN$1 "
}
given() {
    [[ "$SEEN" == *" $1 "* ]]
}

CHECK=0
REPLACE=0
REPO=""
REGION=""
NAMESPACE=""
AUTO_PLAN=""
PAUSED=""
REVIEWERS=()
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --check)
            once --check
            CHECK=1
            shift
            continue
            ;;
        --replace-reviewers)
            once --replace-reviewers
            REPLACE=1
            shift
            continue
            ;;
        --repo | --region | --image-namespace | --reviewer | --auto-plan | --paused) ;;
        *) usage ;;
    esac
    [[ "$#" -ge 2 ]] || usage
    case "$1" in
        --repo) once --repo; REPO="$2" ;;
        --region) once --region; REGION="$2" ;;
        --image-namespace) once --image-namespace; NAMESPACE="$2" ;;
        --auto-plan) once --auto-plan; AUTO_PLAN="$2" ;;
        --paused) once --paused; PAUSED="$2" ;;
        --reviewer) SEEN="$SEEN--reviewer "; REVIEWERS+=("$2") ;;
    esac
    shift 2
done

given --repo || bad "--repo <owner/name> is required"
valid "$RE_REPO" "$REPO" || bad "--repo must look like owner/name"
if given --region; then
    valid "$RE_REGION" "$REGION" || bad "--region must look like a region name"
fi
if given --image-namespace; then
    valid "$RE_NAMESPACE" "$NAMESPACE" || bad "--image-namespace must look like ghcr.io/<lowercase owner>"
fi
if given --auto-plan; then
    [[ "$TARGET" == "production" ]] || bad "--auto-plan applies to production only"
    [[ "$AUTO_PLAN" == "true" || "$AUTO_PLAN" == "false" ]] || bad "--auto-plan must be true or false"
fi
if given --paused; then
    [[ "$TARGET" == "staging" ]] || bad "--paused applies to staging only"
    [[ "$PAUSED" == "true" || "$PAUSED" == "false" ]] || bad "--paused must be true or false"
fi
if given --reviewer; then
    [[ "$TARGET" == "production" ]] || bad "--reviewer applies to production only"
    [[ "${#REVIEWERS[@]}" -le "$MAX_REVIEWERS" ]] || bad "at most $MAX_REVIEWERS reviewers may be given"
    LOWER=" "
    for reviewer in "${REVIEWERS[@]}"; do
        valid "$RE_REVIEWER" "$reviewer" || bad "--reviewer must be a GitHub login"
        [[ "$LOWER" != *" ${reviewer,,} "* ]] || bad "a reviewer may be given only once"
        LOWER="$LOWER${reviewer,,} "
    done
fi
if [[ "$REPLACE" -eq 1 ]]; then
    [[ "$TARGET" == "production" ]] || bad "--replace-reviewers applies to production only"
    [[ "$CHECK" -eq 0 ]] || bad "--replace-reviewers does not apply to --check (check shows the difference anyway)"
fi

if [[ "$TARGET" == "production" ]]; then
    SWITCH_GIVEN="$AUTO_PLAN"
else
    SWITCH_GIVEN="$PAUSED"
fi

command -v jq > /dev/null || die "jq is required"
command -v gh > /dev/null || die "the gh CLI is required"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---- gh calls ---------------------------------------------------------------------------------
GH_OUT=""
GH_ERR=""

# gh_call <args>: stdout in GH_OUT, the error text in GH_ERR; returns gh's status. Only reads are
# made in check mode: every call here is a plain `gh api` GET.
gh_call() {
    local status=0
    GH_OUT="$(gh "$@" 2> "$WORK/gh.err")" || status="$?"
    GH_ERR="$(< "$WORK/gh.err")"
    return "$status"
}

# first_error_line: the first non-blank line of GH_ERR, or a fallback when there is none.
first_error_line() {
    local first
    first="$(grep -m1 -v '^[[:space:]]*$' <<< "$GH_ERR" || true)"
    echo "${first:-no error text from gh}"
}

# ---- the account: login and admin permission, before anything else is read --------------------
gh_call api "repos/$REPO" --jq '{id, admin: .permissions.admin}' || die "could not read repository $REPO: $(first_error_line)"
jq -e '.admin == true' <<< "$GH_OUT" > /dev/null 2>&1 || die "gh is not logged in as an administrator of $REPO"

# ---- the step printer -------------------------------------------------------------------------
STEP=0
TOTAL=0
BAD=0

# report <what> <result>: one line per item. A result that starts with `ok` is fine.
report() {
    STEP="$((STEP + 1))"
    echo "[$STEP/$TOTAL] $1: $2"
    [[ "$2" == "ok" || "$2" == "ok "* ]] || BAD="$((BAD + 1))"
}

# ENV_WRITTEN is 1 while the environment being applied has been written (the PUT, a policy POST
# or DELETE): a later failure then says so, because a second run finishes what the first began.
ENV_WRITTEN=0

# gh_fail <what>: a call that failed ends the run, naming the item that was being read or written.
gh_fail() {
    local suffix=""
    [[ "$ENV_WRITTEN" -eq 0 ]] || suffix=" (the environment was written; run again to finish)"
    echo "[$((STEP + 1))/$TOTAL] $1: failed: $(first_error_line)$suffix" >&2
    exit 1
}

# ---- read every environment of the target before reporting anything ---------------------------
# ENV_JSON[<name>] is the environment's JSON, or empty when it does not exist (a 404).
declare -A ENV_JSON=()
for entry in "${ENVIRONMENTS[@]}"; do
    IFS='|' read -r name _ _ <<< "$entry"
    if gh_call api "repos/$REPO/environments/$name"; then
        ENV_JSON["$name"]="$GH_OUT"
    elif [[ "$GH_ERR" == *"(HTTP 404)"* ]]; then
        ENV_JSON["$name"]=""
    else
        die "could not read environment $name: $(first_error_line)"
    fi
done

env_exists() {
    [[ -n "${ENV_JSON[$1]}" ]]
}

# env_has <name> <jq expression>: true when the expression holds for the environment's JSON.
env_has() {
    jq -e "$2" <<< "${ENV_JSON[$1]}" > /dev/null 2>&1
}

# list_policies <name>: the environment's branch policies as a JSON array in POLICIES. Only for an
# environment whose own read says custom policies are on (the list of any other is a 404).
POLICIES="[]"
list_policies() {
    gh_call api --paginate "repos/$REPO/environments/$1/deployment-branch-policies?per_page=100" --jq '.branch_policies[] | {id, name, type}' || gh_fail "environment $1 branch policy"
    POLICIES="$(jq -cs '.' <<< "$GH_OUT")"
}

# secret_listed <env> <name>: the environment exists and lists the secret by name. Secrets cannot be
# read back, so only names are known; each environment's list is read once, and an environment that
# does not exist yet has none (the answer is kept, so it is not read again once it has been made).
declare -A SECRET_NAMES=()
secret_listed() {
    if [[ -z "${SECRET_NAMES[$1]+x}" ]]; then
        if env_exists "$1"; then
            gh_call api --paginate "repos/$REPO/environments/$1/secrets?per_page=100" --jq '.secrets[].name' || gh_fail "secret $2 on $1"
            SECRET_NAMES["$1"]=$'\n'"$GH_OUT"$'\n'
        else
            SECRET_NAMES["$1"]=$'\n'
        fi
    fi
    [[ "${SECRET_NAMES[$1]}" == *$'\n'"$2"$'\n'* ]]
}

# read_variables: the repository variables as one JSON object in VARS. The values stay in this
# shell, and are compared, never printed (the switches excepted: their whole content is true or
# false).
VARS="{}"
read_variables() {
    gh_call api --paginate "repos/$REPO/actions/variables?per_page=30" --jq '.variables[] | {name, value}' || gh_fail "repository variables"
    VARS="$(jq -cs 'map({(.name): .value}) | add // {}' <<< "$GH_OUT" 2> /dev/null)" || die "could not read the repository variables"
}

var_present() {
    jq -e --arg name "$1" 'has($name)' <<< "$VARS" > /dev/null
}

var_value() {
    jq -j --arg name "$1" '.[$name] // ""' <<< "$VARS"
}

# variable_want <kind>: the value the flag for this kind of variable gave, in WANT (empty when the
# flag was not given).
variable_want() {
    case "$1" in
        namespace) WANT="$NAMESPACE" ;;
        region) WANT="$REGION" ;;
        *) WANT="$SWITCH_GIVEN" ;;
    esac
}

# switch_shown <name>: SHOWN is the live value of a switch, true or false, or `unset: off` when the
# variable does not exist; UNRECOGNISED is 1 when it holds anything else (the value is not kept).
SHOWN=""
UNRECOGNISED=0
switch_shown() {
    SHOWN="unset: off"
    UNRECOGNISED=0
    if var_present "$1"; then
        SHOWN="$(var_value "$1")"
        if [[ "$SHOWN" != "true" && "$SHOWN" != "false" ]]; then
            SHOWN="not true or false"
            UNRECOGNISED=1
        fi
    fi
}

# ---- state probes: STATE becomes ok, missing or differs ---------------------------------------
# policy_state <name>: custom policies on, protected branches off, and exactly `main` (branch).
# An environment whose own read says `deployment_branch_policy` is null has no policies and its
# list is a 404, so the verdict is `missing` without listing; the list is requested only when the
# read says custom policies are on.
policy_state() {
    local policies
    STATE=missing
    env_exists "$1" || return 0
    env_has "$1" '.deployment_branch_policy == null' && return 0
    if ! env_has "$1" '.deployment_branch_policy | .custom_branch_policies == true and .protected_branches == false'; then
        STATE=differs
        return 0
    fi
    list_policies "$1"
    policies="$POLICIES"
    if jq -e 'length == 0' <<< "$policies" > /dev/null; then
        STATE=missing
    elif jq -e 'length == 1 and .[0].name == "main" and .[0].type == "branch"' <<< "$policies" > /dev/null; then
        STATE=ok
    else
        STATE=differs
    fi
}

# live_reviewers <name>: the required reviewers' logins, lowercase, sorted, as a JSON array.
live_reviewers() {
    jq -c '[.protection_rules[]? | select(.type == "required_reviewers") | .reviewers[]? | (.reviewer.login // .reviewer.slug // "unknown") | ascii_downcase] | sort' <<< "${ENV_JSON[$1]}"
}

# given_reviewers: the --reviewer logins, lowercase, sorted, as a JSON array.
given_reviewers() {
    if [[ "${#REVIEWERS[@]}" -eq 0 ]]; then
        echo "[]"
    else
        printf '%s\n' "${REVIEWERS[@]}" | jq -R 'ascii_downcase' | jq -cs 'sort'
    fi
}

# ---- apply: environments, reviewers and branch policies ---------------------------------------
# Lowercase logins of every reviewer seen, by id, for messages (logins are public account names).
LOGIN_MAP="{}"
declare -A WANT_SET=()
declare -A WANT_BYPASS=()
GIVEN_SET="[]"

# remember_logins <name>: the logins of the environment's required reviewers, by id.
remember_logins() {
    LOGIN_MAP="$(jq -c --argjson map "$LOGIN_MAP" '$map + ([.protection_rules[]? | select(.type == "required_reviewers") | .reviewers[]? | {key: (.reviewer.id | tostring), value: ((.reviewer.login // .reviewer.slug // "unknown") | ascii_downcase)}] | from_entries)' <<< "${ENV_JSON[$1]}")"
}

# reviewer_set <name>: the required reviewers as `[{type, id}]`, sorted by id.
reviewer_set() {
    jq -c '[.protection_rules[]? | select(.type == "required_reviewers") | .reviewers[]? | {type, id: .reviewer.id}] | sort_by(.id)' <<< "${ENV_JSON[$1]}"
}

# same_ids <set> <set>: both reviewer sets hold the same ids.
same_ids() {
    jq -ne --argjson a "$1" --argjson b "$2" '($a | map(.id) | sort) == ($b | map(.id) | sort)' > /dev/null
}

# logins_of <set>: the logins, sorted, or `none`.
logins_of() {
    jq -r --argjson map "$LOGIN_MAP" '[.[] | $map[(.id | tostring)] // ("user-" + (.id | tostring))] | sort | if length == 0 then "none" else join(", ") end' <<< "$1"
}

# env_value <name> <field>: one setting of an environment as read, printed as text.
env_value() {
    case "$2" in
        wait_timer) jq -r '[.protection_rules[]? | select(.type == "wait_timer") | .wait_timer | numbers] | (first // 0)' <<< "${ENV_JSON[$1]}" ;;
        prevent_self_review) jq -r '[.protection_rules[]? | select(.type == "required_reviewers") | .prevent_self_review == true] | any' <<< "${ENV_JSON[$1]}" ;;
        can_admins_bypass) jq -r '.can_admins_bypass != false' <<< "${ENV_JSON[$1]}" ;;
        custom_branch_policies) jq -r '.deployment_branch_policy.custom_branch_policies == true' <<< "${ENV_JSON[$1]}" ;;
        protected_branches) jq -r '.deployment_branch_policy.protected_branches == true' <<< "${ENV_JSON[$1]}" ;;
    esac
}

# write_failed <name> <field> <actual> <intended>: the re-read after a write found a difference.
# Field names, counts, booleans and logins only: never a secret.
write_failed() {
    echo "[$((STEP + 1))/$TOTAL] environment $1: failed: $2 is $3 after the write, expected $4" >&2
    exit 1
}

# step <what> <result>: one line per item of the apply.
step() {
    STEP="$((STEP + 1))"
    echo "[$STEP/$TOTAL] $1: $2"
}

# resolve_given: the --reviewer logins as `[{type, id}]` in GIVEN_SET; an unknown login or an
# account that is not a user stops the run (nothing has been written yet).
resolve_given() {
    local reviewer
    [[ "${#REVIEWERS[@]}" -gt 0 ]] || return 0
    for reviewer in "${REVIEWERS[@]}"; do
        gh_call api "users/$reviewer" --jq '{id, type, login}' || die "could not look up reviewer $reviewer: $(first_error_line)"
        jq -e '.type == "User" and (.id | type == "number") and (.login | type == "string")' <<< "$GH_OUT" > /dev/null 2>&1 || die "reviewer $reviewer is not a user account"
        GIVEN_SET="$(jq -c --argjson cur "$GIVEN_SET" '$cur + [{type: "User", id: .id}]' <<< "$GH_OUT")"
        LOGIN_MAP="$(jq -c --argjson map "$LOGIN_MAP" '$map + {(.id | tostring): (.login | ascii_downcase)}' <<< "$GH_OUT")"
    done
    GIVEN_SET="$(jq -c 'sort_by(.id)' <<< "$GIVEN_SET")"
}

# decide_all: every refusal and every wanted value for the whole target, before the first write.
# production-plan is processed before production, so a refusal about production must not come
# after production-plan has already been written.
decide_all() {
    local entry name reviewers bypass live_set want_set
    for entry in "${ENVIRONMENTS[@]}"; do
        IFS='|' read -r name reviewers bypass <<< "$entry"
        live_set="[]"
        if env_exists "$name"; then
            live_set="$(reviewer_set "$name")"
            remember_logins "$name"
        fi
        want_set="$live_set"
        if [[ "$reviewers" == "required" && "${#REVIEWERS[@]}" -gt 0 ]]; then
            want_set="$GIVEN_SET"
        fi
        WANT_SET["$name"]="$want_set"
        if [[ "$reviewers" == "required" ]]; then
            if jq -e 'length == 0' <<< "$want_set" > /dev/null; then
                die "$name needs at least one reviewer: pass --reviewer <login>"
            fi
            if env_exists "$name" && ! same_ids "$want_set" "$live_set"; then
                echo "note: reviewers on $name: $(logins_of "$live_set") -> $(logins_of "$want_set")"
                [[ "$REPLACE" -eq 1 ]] || die "refusing to change the reviewers of $name: pass --replace-reviewers to allow it"
            fi
        fi
        if [[ "$bypass" == "false" ]]; then
            WANT_BYPASS["$name"]=false
        elif env_exists "$name"; then
            WANT_BYPASS["$name"]="$(env_value "$name" can_admins_bypass)"
        else
            WANT_BYPASS["$name"]=true
        fi
    done
}

# verify_write <name> <reviewer set> <wait timer> <self-review> <admin bypass>: re-read the
# environment after a PUT and stop when a field is not what was intended. GitHub's behaviour for
# the fields a PUT does not mention is not documented, so the re-read is the guard.
verify_write() {
    local name="$1" want_set="$2" actual
    gh_call api "repos/$REPO/environments/$name" || gh_fail "environment $name"
    ENV_JSON["$name"]="$GH_OUT"
    remember_logins "$name"
    actual="$(reviewer_set "$name")"
    same_ids "$actual" "$want_set" || write_failed "$name" reviewers "$(logins_of "$actual")" "$(logins_of "$want_set")"
    actual="$(env_value "$name" wait_timer)"
    [[ "$actual" == "$3" ]] || write_failed "$name" wait_timer "$actual" "$3"
    actual="$(env_value "$name" prevent_self_review)"
    [[ "$actual" == "$4" ]] || write_failed "$name" prevent_self_review "$actual" "$4"
    actual="$(env_value "$name" can_admins_bypass)"
    [[ "$actual" == "$5" ]] || write_failed "$name" can_admins_bypass "$actual" "$5"
    actual="$(env_value "$name" custom_branch_policies)"
    [[ "$actual" == "true" ]] || write_failed "$name" custom_branch_policies "$actual" true
    actual="$(env_value "$name" protected_branches)"
    [[ "$actual" == "false" ]] || write_failed "$name" protected_branches "$actual" false
}

# sync_policies <name>: `main` (branch) exists, created first; then every other policy is deleted.
# Sets POLICY_WRITES to 1 when something was written.
POLICY_WRITES=0
sync_policies() {
    local name="$1" ids pid pname ptype
    list_policies "$name"
    if ! jq -e 'any(.[]; .name == "main" and .type == "branch")' <<< "$POLICIES" > /dev/null; then
        gh_call api --method POST "repos/$REPO/environments/$name/deployment-branch-policies" --input - <<< '{"name":"main","type":"branch"}' || gh_fail "environment $name branch policy"
        ENV_WRITTEN=1
        echo "note: $name policy main: created"
        POLICY_WRITES=1
    fi
    ids="$(jq -r '.[] | select(.name != "main" or .type != "branch") | .id | tostring' <<< "$POLICIES")"
    while IFS= read -r pid; do
        [[ -n "$pid" ]] || continue
        valid '^[0-9]+$' "$pid" || die "environment $name has a branch policy with an unreadable id"
        pname="$(jq -r --argjson id "$pid" '.[] | select(.id == $id) | .name' <<< "$POLICIES")"
        ptype="$(jq -r --argjson id "$pid" '.[] | select(.id == $id) | .type' <<< "$POLICIES")"
        gh_call api --method DELETE "repos/$REPO/environments/$name/deployment-branch-policies/$pid" || gh_fail "environment $name branch policy"
        ENV_WRITTEN=1
        # A policy that is not a branch policy (a tag policy named main, say) shows its type.
        [[ "$ptype" == "branch" ]] || pname="$pname ($ptype)"
        echo "note: $name policy $pname: removed"
        POLICY_WRITES=1
    done <<< "$ids"
}

# ---- apply: secrets ---------------------------------------------------------------------------
# SECRET_VALUE[<name>] holds each secret value that will be written, SECRET_ROTATE[<name>] is 1
# when it came from SETUP_<NAME> (an explicit rotation, written even where the secret exists) and
# 0 when it came from the prompt (written only where the secret is missing). Values live in these
# shell variables only: never exported, never printed, never on a command line.
declare -A SECRET_VALUE=()
declare -A SECRET_ROTATE=()
SECRET_RE=""
SECRET_KIND=""
MISSING=0

# secret_pattern <name>: the pattern and the kind of value a secret holds.
secret_pattern() {
    case "$1" in
        *_ROLE_ARN)
            SECRET_RE="$RE_ROLE_ARN"
            SECRET_KIND="role ARN"
            ;;
        *_INSTANCE_ID)
            SECRET_RE="$RE_INSTANCE_ID"
            SECRET_KIND="managed-node id"
            ;;
        PROD_CONSOLE_ORIGIN)
            SECRET_RE="$RE_ORIGIN"
            SECRET_KIND="origin"
            ;;
        *) die "no value pattern is known for secret $1" ;;
    esac
}

# prepare_secrets: decide, before any write, where each secret's value comes from and validate it.
# A secret that exists everywhere it belongs is not prompted for. The message of a bad value names
# the variable and the kind of value, never the value.
prepare_secrets() {
    local entry env name var value from_setup needed other other_env other_name interactive=0 seen=" "
    if [[ -t 0 && -t 2 ]]; then
        interactive=1
    fi
    for entry in "${SECRETS[@]}"; do
        IFS='|' read -r env name <<< "$entry"
        [[ "$seen" != *" $name "* ]] || continue
        seen="$seen$name "
        var="SETUP_$name"
        value=""
        from_setup=0
        if [[ -n "${!var+x}" ]]; then
            value="${!var}"
            from_setup=1
        else
            needed=0
            for other in "${SECRETS[@]}"; do
                IFS='|' read -r other_env other_name <<< "$other"
                if [[ "$other_name" == "$name" ]] && ! secret_listed "$other_env" "$name"; then
                    needed=1
                fi
            done
            if [[ "$needed" -eq 1 && "$interactive" -eq 1 ]]; then
                read -rs -p "Value for $name (hidden; Enter skips it): " value || value=""
                echo >&2
            fi
        fi
        if [[ "$from_setup" -eq 0 && -z "$value" ]]; then
            continue
        fi
        secret_pattern "$name"
        if ! valid "$SECRET_RE" "$value"; then
            if [[ "$from_setup" -eq 1 ]]; then
                bad "$var is not a valid $SECRET_KIND"
            fi
            bad "the value given for $name is not a valid $SECRET_KIND"
        fi
        SECRET_VALUE["$name"]="$value"
        SECRET_ROTATE["$name"]="$from_setup"
    done
}

# set_secret <env> <name>: the value reaches `gh secret set` on stdin only (printf is a builtin, so
# it is not on any command line either); gh's output is discarded except its error text, which
# cannot hold the value.
set_secret() {
    local status=0
    printf '%s' "${SECRET_VALUE[$2]}" | gh secret set "$2" --env "$1" --repo "$REPO" > /dev/null 2> "$WORK/gh.err" || status="$?"
    GH_ERR="$(< "$WORK/gh.err")"
    return "$status"
}

# apply_secrets: one line per secret of the target, after every environment exists.
apply_secrets() {
    local entry env name what write
    for entry in "${SECRETS[@]}"; do
        IFS='|' read -r env name <<< "$entry"
        what="secret $name on $env"
        write=0
        if [[ -n "${SECRET_VALUE[$name]+x}" ]]; then
            if [[ "${SECRET_ROTATE[$name]}" -eq 1 ]] || ! secret_listed "$env" "$name"; then
                write=1
            fi
        fi
        if [[ "$write" -eq 1 ]]; then
            set_secret "$env" "$name" || gh_fail "$what"
            step "$what" set
        elif secret_listed "$env" "$name"; then
            step "$what" unchanged
        else
            step "$what" missing
            MISSING="$((MISSING + 1))"
            echo "note: give SETUP_$name or run on a terminal"
        fi
    done
}

# ---- apply: variables -------------------------------------------------------------------------
# apply_variables: one line per variable of the target. A variable is set when its flag was given
# and it is absent or different; a required one that is absent and not given is missing. Values
# are never printed, except for the two switches, whose whole content is true or false.
apply_variables() {
    local entry name kind flag needs_set
    for entry in "${VARIABLES[@]}"; do
        IFS='|' read -r name kind _ <<< "$entry"
        variable_want "$kind"
        needs_set=0
        if [[ -n "$WANT" ]]; then
            if ! var_present "$name" || [[ "$(var_value "$name")" != "$WANT" ]]; then
                needs_set=1
            fi
        fi
        if [[ "$needs_set" -eq 1 ]]; then
            gh_call variable set "$name" --repo "$REPO" --body "$WANT" || gh_fail "variable $name"
            if [[ "$kind" == "switch" ]]; then
                step "variable $name" "set ($WANT)"
            else
                step "variable $name" set
            fi
        elif [[ "$kind" == "switch" ]]; then
            switch_shown "$name"
            step "variable $name" "unchanged ($SHOWN)"
        elif var_present "$name"; then
            step "variable $name" unchanged
        else
            step "variable $name" missing
            MISSING="$((MISSING + 1))"
            flag="--region"
            [[ "$kind" != "namespace" ]] || flag="--image-namespace"
            echo "note: give $flag to set $name"
        fi
    done
}

# run_apply: every refusal and every input check first, then for each environment of the target in
# order, then the secrets, then the variables.
run_apply() {
    local entry name existed result need want_set want_bypass timer prevent body live_bypass
    local environment_count="${#ENVIRONMENTS[@]}" secret_count="${#SECRETS[@]}" variable_count="${#VARIABLES[@]}"
    TOTAL="$((environment_count + secret_count + variable_count))"
    resolve_given
    decide_all
    prepare_secrets
    read_variables
    for entry in "${ENVIRONMENTS[@]}"; do
        IFS='|' read -r name _ _ <<< "$entry"
        want_set="${WANT_SET[$name]}"
        want_bypass="${WANT_BYPASS[$name]}"
        existed=0
        need=0
        result=unchanged
        timer=0
        prevent=false
        ENV_WRITTEN=0
        if env_exists "$name"; then
            existed=1
            timer="$(env_value "$name" wait_timer)"
            prevent="$(env_value "$name" prevent_self_review)"
            live_bypass="$(env_value "$name" can_admins_bypass)"
            if [[ "$live_bypass" != "$want_bypass" ]]; then
                need=1
                echo "note: $name admin bypass: $live_bypass -> $want_bypass (changed)"
            fi
            same_ids "$want_set" "$(reviewer_set "$name")" || need=1
            [[ "$(env_value "$name" custom_branch_policies)" == "true" && "$(env_value "$name" protected_branches)" == "false" ]] || need=1
        else
            need=1
            result=created
        fi
        if [[ "$need" -eq 1 ]]; then
            # The complete desired state, whatever GitHub does with a field a PUT leaves out.
            body="$(jq -nc --argjson reviewers "$want_set" --argjson timer "$timer" --argjson prevent "$prevent" --argjson bypass "$want_bypass" '{reviewers: $reviewers, wait_timer: $timer, prevent_self_review: $prevent, can_admins_bypass: $bypass, deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}}')"
            gh_call api --method PUT "repos/$REPO/environments/$name" --input - <<< "$body" || gh_fail "environment $name"
            ENV_WRITTEN=1
            verify_write "$name" "$want_set" "$timer" "$prevent" "$want_bypass"
            [[ "$existed" -eq 0 ]] || result=updated
        fi
        # The policy list exists only while custom policies are on: after the write above, or
        # when the environment's own read already said so.
        POLICY_WRITES=0
        sync_policies "$name"
        [[ "$POLICY_WRITES" -eq 0 || "$result" == "created" ]] || result=updated
        step "environment $name" "$result"
        ENV_WRITTEN=0
    done
    apply_secrets
    apply_variables
    if [[ "$MISSING" -gt 0 ]]; then
        echo "apply: $MISSING item(s) still missing"
        exit 1
    fi
    echo "apply: everything is in place"
}

if [[ "$CHECK" -eq 0 ]]; then
    run_apply
    exit 0
fi

# ---- the check --------------------------------------------------------------------------------
for entry in "${ENVIRONMENTS[@]}"; do
    IFS='|' read -r _ _ bypass <<< "$entry"
    TOTAL="$((TOTAL + 3))"
    [[ "$bypass" != "false" ]] || TOTAL="$((TOTAL + 1))"
done
SECRET_COUNT="${#SECRETS[@]}"
VARIABLE_COUNT="${#VARIABLES[@]}"
TOTAL="$((TOTAL + SECRET_COUNT + VARIABLE_COUNT))"

GIVEN="$(given_reviewers)"

for entry in "${ENVIRONMENTS[@]}"; do
    IFS='|' read -r name reviewers bypass <<< "$entry"
    if env_exists "$name"; then report "environment $name" ok; else report "environment $name" missing; fi

    policy_state "$name"
    report "environment $name branch policy" "$STATE"

    LIVE="[]"
    if env_exists "$name"; then LIVE="$(live_reviewers "$name")"; fi
    COUNT="$(jq 'length' <<< "$LIVE")"
    if [[ "$reviewers" == "required" ]]; then
        if [[ "$COUNT" -eq 0 ]]; then
            report "environment $name reviewers" missing
        elif [[ "${#REVIEWERS[@]}" -gt 0 && "$LIVE" != "$GIVEN" ]]; then
            echo "note: reviewers on $name: $(jq -r 'join(", ")' <<< "$LIVE") -> $(jq -r 'join(", ")' <<< "$GIVEN")"
            report "environment $name reviewers" differs
        else
            report "environment $name reviewers" ok
        fi
    else
        report "environment $name reviewers" ok
        if [[ "$COUNT" -gt 0 ]]; then
            echo "note: $name has $COUNT required reviewer(s): every job that uses it waits on that click"
        fi
    fi

    if env_exists "$name"; then
        jq -r --arg name "$name" '"note: \($name) can_admins_bypass=\(.can_admins_bypass == true) prevent_self_review=\([.protection_rules[]? | select(.type == "required_reviewers") | .prevent_self_review == true] | any) wait_timer=\([.protection_rules[]? | select(.type == "wait_timer") | .wait_timer | numbers] | first // 0)"' <<< "${ENV_JSON[$name]}"
    fi
    if [[ "$bypass" == "false" ]]; then
        if ! env_exists "$name"; then
            report "environment $name admin bypass" missing
        elif env_has "$name" '.can_admins_bypass == false'; then
            report "environment $name admin bypass" ok
        else
            report "environment $name admin bypass" differs
        fi
    fi
done

# Secrets cannot be read back: only their names are listed, per environment that exists.
for entry in "${SECRETS[@]}"; do
    IFS='|' read -r env name <<< "$entry"
    if secret_listed "$env" "$name"; then
        report "secret $name on $env" ok
    else
        report "secret $name on $env" missing
    fi
done

# Variables: the values are compared, never printed (the switches excepted).
read_variables

for entry in "${VARIABLES[@]}"; do
    IFS='|' read -r name kind _ <<< "$entry"
    variable_want "$kind"
    if [[ "$kind" != "switch" ]]; then
        if ! var_present "$name"; then
            report "variable $name" missing
        elif [[ -n "$WANT" && "$(var_value "$name")" != "$WANT" ]]; then
            report "variable $name" differs
        else
            report "variable $name" ok
        fi
        continue
    fi
    # A switch: unset means off. It is the one value printed.
    switch_shown "$name"
    if [[ "$UNRECOGNISED" -eq 1 ]]; then
        report "variable $name" "differs ($SHOWN)"
    elif [[ -n "$WANT" && "$SHOWN" != "$WANT" ]]; then
        report "variable $name" "differs ($SHOWN)"
    else
        report "variable $name" "ok ($SHOWN)"
    fi
done

if [[ "$BAD" -eq 0 ]]; then
    echo "check: everything is ok"
else
    echo "check: $BAD item(s) not ok"
    exit 1
fi
