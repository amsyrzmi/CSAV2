#!/bin/bash
# Create or check the AWS side of a deploy target, from AWS CloudShell (admin login), in a
# `git clone --depth 1` of the repository; the templates are read relative to this script:
#
#   setup-target.sh <staging|production> activate --region <region>
#   setup-target.sh <staging|production> <apply|check> --region <region> --node <mi-id>
#       --subject <prefix> [--subject <prefix> ...] [--allow-subject-removal]
#
#   activate creates the node's service role if it is missing (trust: Systems Manager in this
#          account and region, as in deploy/PRODUCTION.md step 1), attaches
#          AmazonSSMManagedInstanceCore, and creates one single-use activation that expires in
#          four hours. It prints the activation id and code once: type the code at the hidden
#          prompt on the host and never paste it anywhere else.
#   apply  idempotent: creates the GitHub OIDC provider if it is missing; for each deploy role,
#          creates it (or updates its trust) and puts its inline policy; for each Systems Manager
#          document, creates it (or, when the default version's content differs, updates it and
#          makes the new version the default). Anything already right is left alone: a second
#          run makes no write call. An interrupted document update (the new version exists but is
#          not the default) is finished by the next run. Ends by printing the role ARNs, which
#          contain the account id: use them in CloudShell and in GitHub environment secrets only.
#          Run `check` before `apply` (especially for production, where a mistyped --subject
#          rewrites a role's trust policy).
#   check  read-only (get, list and describe calls only): reports ok, missing or differs for the
#          provider, each role's trust and inline policy, each document's default version, and
#          the node's ping status. Exits 0 only when everything is ok.
#
# <subject> is the OIDC subject prefix GitHub issues for the repository (docs: deploy/PRODUCTION.md,
# step 3). --subject may be repeated: pass every subject the role must trust, including the subjects
# of a planned repository or organisation move (at most 5, no duplicates). With one subject each
# role's `sub` condition is a plain string; with several it is a list of `<subject>:environment:<env>`
# strings in the order given. check compares the live list as a set (sorted, duplicates removed: the
# order stored in AWS does not matter, and apply leaves an equal set alone), but a string is never
# the same as a one-element list. Production's live roles trust several subjects, so
# `production check` must be given all of them.
#
# apply refuses to REMOVE a subject a live role trusts: before its first write it reads each
# existing role and, when the live `sub` values are not all among the wanted ones, prints one
# `refused:` line per role to stderr and exits 1 having written nothing. Pass
# --allow-subject-removal (apply only; activate and check accept it and ignore it) when the removal
# is intended; apply then prints a `removing` line per role before its steps. Subjects the live role
# lacks are announced with an `adding` line (with or without the flag).
# `apply` replaces the whole trust document of a role, not only its subject: any extra
# statement or condition in a live trust is overwritten, and `check` reports it as `differs`.
# The guard reads the `sub` key in any letter case (IAM condition keys are case-insensitive), under
# any statement and any condition operator.
# Every input is validated before the first AWS call, and nothing is run through eval.
# The templates are copied to a temporary directory (removed on exit), filled there, and refused
# if any <placeholder> is left; nothing is written under the repository. Of AWS's own output only
# the role ARNs are shown, and the live subjects that match the subject shape (in the `refused`,
# `removing` and `adding` lines; any other live value is shown as `<unrecognised value>`);
# each step prints `[n/total] what: result` before and after its calls.
set -euo pipefail
# Regex ranges follow the locale (a UTF-8 locale lets Unicode digits through [0-9]), so pin it
# before any validation.
export LC_ALL=C
export AWS_PAGER=""

# ---- the two targets ------------------------------------------------------------------------
# NODE_ROLE is the Systems Manager service role of the node (activate); INSTANCE_NAME is the
# name its activation gives the node. Roles are `name|trust template|inline policy name|
# permissions template` and documents are `name|file`, with templates relative to
# deploy/<templates>/.
configure_target() {
    case "$1" in
        production)
            TEMPLATES="production"
            NODE_ROLE="csa-prod-ssm-node"
            INSTANCE_NAME="csa-prod"
            ROLES=(
                "csa-prod-deploy-plan|iam/plan-trust-policy.json|send-plan-document|iam/plan-permissions-policy.json"
                "csa-prod-deploy-apply|iam/apply-trust-policy.json|send-apply-document|iam/apply-permissions-policy.json"
            )
            DOCUMENTS=(
                "csa-production-plan|ssm-document-plan.json"
                "csa-production-apply|ssm-document-apply.json"
            )
            ;;
        staging)
            TEMPLATES="staging"
            NODE_ROLE="csa-staging-ssm-node"
            INSTANCE_NAME="csa-staging"
            ROLES=(
                "csa-staging-deploy|iam/trust-policy.json|send-staging-documents|iam/permissions-policy.json"
            )
            DOCUMENTS=(
                "csa-staging-plan|ssm-document-plan.json"
                "csa-staging-apply|ssm-document-apply.json"
            )
            ;;
        *) return 1 ;;
    esac
}

usage() {
    echo "usage: setup-target.sh <staging|production> activate --region <region>" >&2
    echo "       setup-target.sh <staging|production> <apply|check> --region <region> --node <mi-id> --subject <prefix> [--subject <prefix> ...] [--allow-subject-removal]" >&2
    exit 2
}

# valid <pattern> <value>: the whole value matches and holds no newline.
valid() {
    [[ "$2" =~ $1 && "$2" != *$'\n'* ]]
}

die() {
    echo "$1" >&2
    exit 1
}

# ---- arguments, validated before any AWS call -----------------------------------------------
[[ "$#" -ge 2 ]] || usage
TARGET="$1"
ACTION="$2"
shift 2
configure_target "$TARGET" || usage
case "$ACTION" in
    activate | apply | check) ;;
    *) usage ;;
esac

REGION=""
NODE=""
SUBJECTS=()
NODE_GIVEN=0
SUBJECT_GIVEN=0
ALLOW_REMOVAL=0
while [[ "$#" -gt 0 ]]; do
    # The one flag without a value; every other option takes exactly one.
    if [[ "$1" == "--allow-subject-removal" ]]; then
        ALLOW_REMOVAL=1
        shift
        continue
    fi
    [[ "$#" -ge 2 ]] || usage
    case "$1" in
        --region) REGION="$2" ;;
        --node) NODE="$2"; NODE_GIVEN=1 ;;
        --subject) SUBJECTS+=("$2"); SUBJECT_GIVEN=1 ;;
        *) usage ;;
    esac
    shift 2
done

valid '^[a-z]{2}(-[a-z]+)+-[0-9]$' "$REGION" || { echo "--region must look like a region name" >&2; exit 2; }
# --node and --subject are required for apply and check, and checked whenever they are given.
if [[ "$ACTION" != "activate" || "$NODE_GIVEN" -eq 1 ]]; then
    valid '^mi-[0-9a-f]{17}$' "$NODE" || { echo "--node must be a managed node id (mi- and 17 lowercase hex characters)" >&2; exit 2; }
fi
if [[ "$ACTION" != "activate" || "$SUBJECT_GIVEN" -eq 1 ]]; then
    [[ "${#SUBJECTS[@]}" -ge 1 ]] || { echo "--subject must be the OIDC subject prefix, repo:... (letters, digits and _.@/- only)" >&2; exit 2; }
    for subject in "${SUBJECTS[@]}"; do
        valid '^repo:[A-Za-z0-9_.@/-]+$' "$subject" || { echo "--subject must be the OIDC subject prefix, repo:... (letters, digits and _.@/- only)" >&2; exit 2; }
    done
    SEEN=$'\n'
    for subject in "${SUBJECTS[@]}"; do
        [[ "$SEEN" != *$'\n'"$subject"$'\n'* ]] || { echo "--subject given twice with the same value: $subject" >&2; exit 2; }
        SEEN+="$subject"$'\n'
    done
    [[ "${#SUBJECTS[@]}" -le 5 ]] || { echo "--subject may be given at most 5 times" >&2; exit 2; }
fi
SUBJECT="${SUBJECTS[0]:-}"
command -v jq > /dev/null || die "jq is required"
command -v aws > /dev/null || die "the aws CLI is required"

HERE="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
TEMPLATE_DIR="$ROOT/deploy/$TEMPLATES"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/files"

# ---- the step printer -----------------------------------------------------------------------
STEP=0
TOTAL=0
WHAT=""
BAD=0
STATE=""
AWS_OUT=""
AWS_ERR=""

step_begin() {
    STEP="$((STEP + 1))"
    WHAT="$1"
    if [[ "$ACTION" == "check" ]]; then
        echo "[$STEP/$TOTAL] $WHAT: checking"
    else
        echo "[$STEP/$TOTAL] $WHAT: working"
    fi
}

step_end() {
    echo "[$STEP/$TOTAL] $WHAT: $1"
}

# report <state>: the result of a check step.
report() {
    step_end "$1"
    [[ "$1" == "ok" ]] || BAD="$((BAD + 1))"
}

# ---- AWS calls ------------------------------------------------------------------------------
# aws_call <args>: the JSON answer in AWS_OUT, the error text in AWS_ERR; returns aws's status.
aws_call() {
    local status=0
    AWS_OUT="$(aws "$@" --region "$REGION" --output json 2> "$WORK/aws.err")" || status="$?"
    AWS_ERR="$(< "$WORK/aws.err")"
    return "$status"
}

# first_error_line: the first non-blank line of AWS_ERR (the real CLI starts its text with a blank
# line), or a fallback when there is none.
first_error_line() {
    local first
    first="$(grep -m1 -v '^[[:space:]]*$' <<< "$AWS_ERR" || true)"
    echo "${first:-no error text from aws}"
}

FAIL_HINT=""

aws_fail() {
    echo "[$STEP/$TOTAL] $WHAT: failed: $(first_error_line)" >&2
    if [[ -n "$FAIL_HINT" ]]; then echo "$FAIL_HINT" >&2; fi
    exit 1
}

# aws_must <args>: a call whose failure ends the run.
aws_must() {
    aws_call "$@" || aws_fail
}

# aws_probe <marker> <args>: returns 0 when found, 1 when AWS's error names <marker> (the item
# does not exist); any other failure ends the run.
aws_probe() {
    local marker="$1"
    local status=0
    shift
    aws_call "$@" || status="$?"
    [[ "$status" -ne 0 ]] || return 0
    [[ "$AWS_ERR" == *"$marker"* ]] || aws_fail
    return 1
}

# ---- the account, then the filled templates -------------------------------------------------
if [[ "$ACTION" == "apply" ]]; then
    echo "apply note: run check before apply (read-only); a mistyped --subject rewrites a role's trust policy, so do this especially for production"
fi
aws_call sts get-caller-identity || die "could not read the caller identity: $(first_error_line)"
ACCOUNT="$(jq -r '.Account // empty' <<< "$AWS_OUT")"
valid '^[0-9]{12}$' "$ACCOUNT" || die "the account id from sts get-caller-identity is not 12 digits"
OIDC_ARN="arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com"

# sed_escape <value>: escapes the characters that matter in a sed replacement (the values are
# validated first, so this is a second guard).
sed_escape() {
    printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

copy_template() {
    [[ -f "$TEMPLATE_DIR/$1" ]] || die "missing template deploy/$TEMPLATES/$1"
    mkdir -p "$(dirname "$WORK/files/$1")"
    cp "$TEMPLATE_DIR/$1" "$WORK/files/$1"
}

SUB_KEY="token.actions.githubusercontent.com:sub"

# list_subjects <trust file>: with several subjects the template's `<repo-subject>:environment:<env>`
# string becomes a JSON list of `<subject>:environment:<env>` strings, in the order given. Built
# with jq (the subjects are positional arguments, never spliced into the filter); it runs before the
# sed pass, which then finds no <repo-subject> left in the trust file.
list_subjects() {
    jq --arg key "$SUB_KEY" '(.Statement[].Condition.StringEquals[$key]) |= (if startswith("<repo-subject>:") then ltrimstr("<repo-subject>") as $suffix | [$ARGS.positional[] | . + $suffix] else error("not a <repo-subject> template") end)' "$1" --args "${SUBJECTS[@]}" > "$WORK/trust.tmp" || die "could not put the subjects into a trust template; nothing was changed"
    mv "$WORK/trust.tmp" "$1"
}

fill_templates() {
    local entry name trust policy_name permissions file
    for entry in "${ROLES[@]}"; do
        IFS='|' read -r name trust policy_name permissions <<< "$entry"
        copy_template "$trust"
        copy_template "$permissions"
        if [[ "${#SUBJECTS[@]}" -ge 2 ]]; then list_subjects "$WORK/files/$trust"; fi
    done
    for entry in "${DOCUMENTS[@]}"; do
        IFS='|' read -r name file <<< "$entry"
        copy_template "$file"
    done
    local e_region e_account e_node e_oidc e_subject
    e_region="$(sed_escape "$REGION")"
    e_account="$(sed_escape "$ACCOUNT")"
    e_node="$(sed_escape "$NODE")"
    e_oidc="$(sed_escape "$OIDC_ARN")"
    e_subject="$(sed_escape "$SUBJECT")"
    find "$WORK/files" -type f -name '*.json' -exec sed -i \
        -e "s|<region>|$e_region|g" \
        -e "s|<account-id>|$e_account|g" \
        -e "s|<managed-instance-id>|$e_node|g" \
        -e "s|<oidc-provider-arn>|$e_oidc|g" \
        -e "s|<repo-subject>|$e_subject|g" {} +
    if grep -rn '<[^>]*>' "$WORK/files" > /dev/null; then
        die "a placeholder is left in the filled templates; nothing was changed"
    fi
}

if [[ "$ACTION" != "activate" ]]; then
    fill_templates
fi

# ---- state probes: STATE becomes ok, missing or differs --------------------------------------
# normalised <file>: the JSON of a file in a stable key order, for comparing with what AWS holds.
normalised() {
    jq -S . "$1"
}

# The role trust is compared with its `sub` list sorted and without duplicates (a set), so neither
# the order stored in AWS nor a repeated entry matters (a string stays a string: it is not the same
# as a one-element list).
SET_SUBJECTS='walk(if type == "object" and (.[$key] | type) == "array" then .[$key] |= unique else . end)'

provider_state() {
    aws_must iam list-open-id-connect-providers
    if jq -e --arg arn "$OIDC_ARN" '[.OpenIDConnectProviderList[].Arn] | index($arn) != null' <<< "$AWS_OUT" > /dev/null; then
        STATE=ok
    else
        STATE=missing
    fi
}

# trust_state <role> <template>
trust_state() {
    local have want
    if ! aws_probe NoSuchEntity iam get-role --role-name "$1"; then
        STATE=missing
        return 0
    fi
    have="$(jq -S --arg key "$SUB_KEY" ".Role.AssumeRolePolicyDocument | $SET_SUBJECTS" <<< "$AWS_OUT")"
    want="$(jq -S --arg key "$SUB_KEY" "$SET_SUBJECTS" "$WORK/files/$2")"
    if [[ "$have" == "$want" ]]; then STATE=ok; else STATE=differs; fi
}

# policy_state <role> <policy name> <template>
policy_state() {
    local have want
    if ! aws_probe NoSuchEntity iam get-role-policy --role-name "$1" --policy-name "$2"; then
        STATE=missing
        return 0
    fi
    have="$(jq -S '.PolicyDocument' <<< "$AWS_OUT")"
    want="$(normalised "$WORK/files/$3")"
    if [[ "$have" == "$want" ]]; then STATE=ok; else STATE=differs; fi
}

# document_state <document> <file>: compares the default version's content. When it differs,
# LATEST_MATCH becomes the number of the latest version if that one already holds the right
# content (an update-document that was never followed by update-document-default-version);
# otherwise it is empty.
LATEST_MATCH=""
document_state() {
    local have want
    LATEST_MATCH=""
    if ! aws_probe InvalidDocument ssm get-document --name "$1" --document-format JSON; then
        STATE=missing
        return 0
    fi
    have="$(jq -r '.Content' <<< "$AWS_OUT" | jq -S . 2> /dev/null)" || have="not JSON"
    want="$(normalised "$WORK/files/$2")"
    if [[ "$have" == "$want" ]]; then
        STATE=ok
        return 0
    fi
    STATE=differs
    if aws_probe InvalidDocument ssm get-document --name "$1" --document-version '$LATEST' --document-format JSON; then
        have="$(jq -r '.Content' <<< "$AWS_OUT" | jq -S . 2> /dev/null)" || have="not JSON"
        if [[ "$have" == "$want" ]]; then
            LATEST_MATCH="$(jq -r '.DocumentVersion // empty' <<< "$AWS_OUT")"
            valid '^[0-9]+$' "$LATEST_MATCH" || die "[$STEP/$TOTAL] $WHAT: get-document returned no version number"
        fi
    fi
}

node_state() {
    local ping
    aws_must ssm describe-instance-information --filters "Key=InstanceIds,Values=$NODE"
    ping="$(jq -r '.InstanceInformationList[0].PingStatus // empty' <<< "$AWS_OUT")"
    if [[ -z "$ping" ]]; then
        STATE=missing
    elif [[ "$ping" == "Online" ]]; then
        STATE=ok
    else
        STATE=differs
    fi
}

# ---- apply: the live role trusts are read before anything is written -------------------------
# The subjects of a trust document: every `sub` value under any statement and operator, a string
# as one element and a list as its elements, as a sorted list without duplicates.
# IAM condition keys are case-insensitive, so the key is matched in lower case ($key is given in
# lower case).
SUBJECTS_OF='[.. | objects | to_entries[] | select(.key | ascii_downcase == $key) | .value | if type == "array" then .[] else . end | strings] | unique'
# Four lines: how many live subjects are not wanted, which (only values of the subject shape are
# shown, anything else becomes a fixed text), how many wanted subjects the live role lacks, which.
# \A and \z anchor the whole value: ^ and $ would also match around a newline inside it.
SUBJECT_CHANGES='def shown: map(if test("\\Arepo:[A-Za-z0-9_.@/-]+:environment:[A-Za-z0-9_.-]+\\z") then . else "<unrecognised value>" end) | join(", "); ($live - $want) as $gone | ($want - $live) as $new | ($gone | length), ($gone | shown), ($new | length), ($new | shown)'

# preflight_subjects: for each role that already exists, compares its live subjects with the wanted
# ones. A removal is refused (stderr, exit 1) unless --allow-subject-removal was given; this runs
# before the provider is created, so a refusal leaves nothing half applied. Roles that do not
# exist yet are skipped. Prints the removing and adding notes when it goes on.
preflight_subjects() {
    local entry role trust _ live want
    local -a change
    local refusals="" notes=""
    WHAT="reading the live trust of the deploy roles"
    for entry in "${ROLES[@]}"; do
        IFS='|' read -r role trust _ <<< "$entry"
        aws_probe NoSuchEntity iam get-role --role-name "$role" || continue
        live="$(jq -c --arg key "$SUB_KEY" ".Role.AssumeRolePolicyDocument | $SUBJECTS_OF" <<< "$AWS_OUT")" || die "could not read the live trust of role $role"
        want="$(jq -c --arg key "$SUB_KEY" "$SUBJECTS_OF" "$WORK/files/$trust")" || die "could not read the wanted trust of role $role"
        mapfile -t change < <(jq -nr --argjson live "$live" --argjson want "$want" "$SUBJECT_CHANGES")
        [[ "${#change[@]}" -eq 4 ]] || die "could not compare the subjects of role $role"
        if [[ "${change[0]}" -gt 0 ]]; then
            refusals+="refused: role $role trusts ${change[0]} subject(s) not given: ${change[1]}"$'\n'
            notes+="removing ${change[0]} subject(s) from role $role trust: ${change[1]}"$'\n'
        fi
        if [[ "${change[2]}" -gt 0 ]]; then
            notes+="adding ${change[2]} subject(s) to role $role trust: ${change[3]}"$'\n'
        fi
    done
    if [[ -n "$refusals" && "$ALLOW_REMOVAL" -ne 1 ]]; then
        printf '%s' "$refusals" >&2
        die "give them with --subject, or pass --allow-subject-removal if the removal is intended"
    fi
    printf '%s' "$notes"
}

# ---- activate: the node's service role and a single-use activation --------------------------
if [[ "$ACTION" == "activate" ]]; then
    TOTAL=3

    ROLE_CREATED=0
    step_begin "node role $NODE_ROLE"
    if aws_probe NoSuchEntity iam get-role --role-name "$NODE_ROLE"; then
        step_end exists
    else
        # One line: the quote checker in the tests reads the script line by line.
        jq -n --arg account "$ACCOUNT" --arg region "$REGION" '{Version: "2012-10-17", Statement: [{Effect: "Allow", Principal: {Service: "ssm.amazonaws.com"}, Action: "sts:AssumeRole", Condition: {StringEquals: {"aws:SourceAccount": $account}, ArnEquals: {"aws:SourceArn": ("arn:aws:ssm:" + $region + ":" + $account + ":*")}}}]}' > "$WORK/ssm-node-trust.json"
        aws_must iam create-role --role-name "$NODE_ROLE" \
            --assume-role-policy-document "file://$WORK/ssm-node-trust.json"
        ROLE_CREATED=1
        step_end created
    fi

    step_begin "policy AmazonSSMManagedInstanceCore on $NODE_ROLE"
    aws_must iam attach-role-policy --role-name "$NODE_ROLE" \
        --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
    step_end attached

    step_begin "single-use activation for $INSTANCE_NAME, expires in 4 hours"
    if [[ "$ROLE_CREATED" -eq 1 ]]; then
        FAIL_HINT="if the role was only just created, wait a minute and run the same command again (it will say exists and continue)"
    fi
    EXPIRY="$(date -u -d '+4 hours' +%Y-%m-%dT%H:%M:%SZ)"
    aws_must ssm create-activation --iam-role "$NODE_ROLE" --default-instance-name "$INSTANCE_NAME" \
        --registration-limit 1 --expiration-date "$EXPIRY"
    ACTIVATION_ID="$(jq -r '.ActivationId // empty' <<< "$AWS_OUT")"
    ACTIVATION_CODE="$(jq -r '.ActivationCode // empty' <<< "$AWS_OUT")"
    AWS_OUT=""
    valid '^[0-9a-f-]{36}$' "$ACTIVATION_ID" || die "create-activation returned no activation id"
    valid '^[^[:space:]]+$' "$ACTIVATION_CODE" || die "create-activation returned no activation code"
    step_end created
    echo "Activation id: $ACTIVATION_ID"
    echo "Activation code: $ACTIVATION_CODE"
    echo "type the code at the hidden prompt on the host; never paste it anywhere else"
    exit 0
fi

# ---- the steps ------------------------------------------------------------------------------
# Provider, then for each role its trust and its policy, then each document (and, for check, the
# node): one step each.
role_count="${#ROLES[@]}"
document_count="${#DOCUMENTS[@]}"
TOTAL="$((1 + 2 * role_count + document_count))"
[[ "$ACTION" != "check" ]] || TOTAL="$((TOTAL + 1))"

if [[ "$ACTION" == "apply" ]]; then
    preflight_subjects
fi

step_begin "OIDC provider token.actions.githubusercontent.com"
provider_state
if [[ "$ACTION" == "check" ]]; then
    report "$STATE"
elif [[ "$STATE" == "missing" ]]; then
    aws_must iam create-open-id-connect-provider \
        --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com
    step_end created
else
    step_end unchanged
fi

for entry in "${ROLES[@]}"; do
    IFS='|' read -r role trust policy_name permissions <<< "$entry"

    step_begin "role $role trust"
    trust_state "$role" "$trust"
    if [[ "$ACTION" == "check" ]]; then
        report "$STATE"
    elif [[ "$STATE" == "missing" ]]; then
        aws_must iam create-role --role-name "$role" --assume-role-policy-document "file://$WORK/files/$trust"
        step_end created
    elif [[ "$STATE" == "differs" ]]; then
        aws_must iam update-assume-role-policy --role-name "$role" --policy-document "file://$WORK/files/$trust"
        step_end updated
    else
        step_end unchanged
    fi

    step_begin "role $role policy $policy_name"
    policy_state "$role" "$policy_name" "$permissions"
    if [[ "$ACTION" == "check" ]]; then
        report "$STATE"
    elif [[ "$STATE" == "ok" ]]; then
        step_end unchanged
    else
        aws_must iam put-role-policy --role-name "$role" --policy-name "$policy_name" \
            --policy-document "file://$WORK/files/$permissions"
        if [[ "$STATE" == "missing" ]]; then step_end created; else step_end updated; fi
    fi
done

for entry in "${DOCUMENTS[@]}"; do
    IFS='|' read -r document file <<< "$entry"

    step_begin "document $document"
    document_state "$document" "$file"
    if [[ "$ACTION" == "check" ]]; then
        report "$STATE"
    elif [[ "$STATE" == "missing" ]]; then
        aws_must ssm create-document --name "$document" --content "file://$WORK/files/$file" \
            --document-type Command --document-format JSON
        step_end created
    elif [[ "$STATE" == "differs" ]]; then
        version="$LATEST_MATCH"
        if [[ -z "$version" ]]; then
            aws_must ssm update-document --name "$document" --content "file://$WORK/files/$file" \
                --document-version '$LATEST' --document-format JSON
            version="$(jq -r '.DocumentDescription.DocumentVersion // empty' <<< "$AWS_OUT")"
            valid '^[0-9]+$' "$version" || die "[$STEP/$TOTAL] $WHAT: update-document returned no version number"
        fi
        aws_must ssm update-document-default-version --name "$document" --document-version "$version"
        step_end updated
    else
        step_end unchanged
    fi
done

if [[ "$ACTION" == "check" ]]; then
    step_begin "node $NODE ping status"
    node_state
    report "$STATE"
    if [[ "$BAD" -eq 0 ]]; then
        echo "check: everything is ok"
        exit 0
    fi
    echo "check: $BAD item(s) not ok"
    exit 1
fi

# apply: the role ARNs go into GitHub secrets (see deploy/PRODUCTION.md and deploy/STAGING_PIPELINE.md).
echo "apply note: the role ARNs below contain the account id: use them in CloudShell and in GitHub environment secrets only"
for entry in "${ROLES[@]}"; do
    IFS='|' read -r role _ <<< "$entry"
    aws_must iam get-role --role-name "$role"
    arn="$(jq -r '.Role.Arn // empty' <<< "$AWS_OUT")"
    valid '^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_-]+$' "$arn" || die "get-role returned no role ARN for $role"
    echo "Role ARN $role: $arn"
done
echo "apply complete"
