#!/bin/bash
# Decide which commit production deploys and check it before anything reaches the instance:
#   resolve_commit.sh [<input-sha>]      environment: GH_TOKEN, REPOSITORY (owner/name),
#                                        IMAGE_NAMESPACE (ghcr.io/<lowercase owner>),
#                                        optional REGISTRY_USER and REGISTRY_TOKEN
# With no input the commit is the tip of main (and that tip alone: if it is not staged, nothing
# older is picked). On full success it prints, for $GITHUB_OUTPUT,
#   sha=, python_digest=, frontend_digest=, staged_on=, explicit=true|false
# Checks run in order and the first failure prints one line to stderr and exits non-zero with
# nothing on stdout: 1 the input, 2 the commit is on main, 3 its ci run on main succeeded, 4 it
# has a success status on a staging Deployment (this is the staged gate, since a host given image
# digests skips its own), 5 both images are tagged with the sha in the registry and their digests
# are readable. Every gh or curl failure and every malformed answer fails closed. GH_TOKEN is
# read only by gh and is never printed. The registry token request carries the login
# REGISTRY_USER:REGISTRY_TOKEN when both are set (the workflow's own token, with packages: read)
# and is retried once anonymously if the registry refuses it; with no login it is anonymous. The
# login and the bearer token reach curl on stdin (a curl config), never on a command line, and
# are read with xtrace off, so `bash -x` does not echo them either.
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

# gh_get <path> <failure message> <empty-answer message>: prints the API answer, which must not
# be empty. (jq prints nothing for empty input and exits 0, which would read as "no data".)
gh_get() {
    local out
    out="$(gh api "$1" 2> /dev/null)" || die "$2"
    [[ -n "${out//[[:space:]]/}" ]] || die "$3"
    printf '%s' "$out"
}

[[ "$#" -le 1 ]] || die "usage: resolve_commit.sh [<sha>]"
input="${1:-}"
# ${GH_TOKEN:+set}, not the value: xtrace shows the expanded test, and in the workflow GH_TOKEN is
# the same token as REGISTRY_TOKEN.
[[ -n "${GH_TOKEN:+set}" ]] || die "GH_TOKEN must be set"
[[ -n "${REPOSITORY:-}" ]] && valid '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' "$REPOSITORY" \
    || die "REPOSITORY must be set to owner/name"
# The registry prefix of the app images comes from the repository variable, never from the
# repository owner: a lowercase ghcr.io namespace, no scheme and no trailing slash.
[[ -n "${IMAGE_NAMESPACE:-}" ]] && valid '^ghcr\.io/[a-z0-9][a-z0-9-]*$' "$IMAGE_NAMESPACE" \
    || die "IMAGE_NAMESPACE must be set to ghcr.io/<lowercase owner>"

# login_state: "none" unless REGISTRY_USER and REGISTRY_TOKEN are both set (one alone is not a
# login), "plain" when both hold only characters that cannot end the quoted curl config line or
# start another directive, "bad" otherwise. It runs in a subshell with xtrace off, so tracing the
# script never shows either value.
login_state() {
    { set +x; } 2> /dev/null
    if [[ -z "${REGISTRY_USER:-}" || -z "${REGISTRY_TOKEN:-}" ]]; then
        echo none
    elif valid '^[][A-Za-z0-9_.-]+$' "$REGISTRY_USER" \
        && valid '^[A-Za-z0-9_.~+/=-]+$' "$REGISTRY_TOKEN"; then
        echo plain
    else
        echo bad
    fi
}
registry_login="$(login_state)"
[[ "$registry_login" != "bad" ]] \
    || die "REGISTRY_USER and REGISTRY_TOKEN must hold only plain token characters"

# 1. The commit: the input, or the tip of main.
if [[ -n "$input" ]]; then
    valid '^[0-9a-f]{40}$' "$input" || die "sha must be the full 40 character lowercase commit SHA"
    sha="$input"
    explicit="true"
else
    body="$(gh_get "repos/$REPOSITORY/commits/main" "could not read the tip of main from GitHub" \
        "unexpected answer for the tip of main")"
    sha="$(jq -r '.sha | strings' <<< "$body" 2> /dev/null)" || die "unexpected answer for the tip of main"
    valid '^[0-9a-f]{40}$' "$sha" || die "unexpected answer for the tip of main"
    explicit="false"
fi
sha7="${sha:0:7}"

# 2. The commit is on main: main is ahead of it, or it is main.
body="$(gh_get "repos/$REPOSITORY/compare/$sha...main" "could not compare $sha7 with main" \
    "unexpected answer comparing $sha7 with main")"
status="$(jq -r '.status | strings' <<< "$body" 2> /dev/null)" || die "unexpected answer comparing $sha7 with main"
[[ "$status" == "ahead" || "$status" == "identical" ]] || die "commit $sha7 is not on main"

# 3. A completed ci run for this sha on main (push) concluded success; any such run is enough.
body="$(gh_get "repos/$REPOSITORY/actions/workflows/ci.yml/runs?head_sha=$sha&branch=main&event=push" \
    "could not read the ci runs for $sha7 from GitHub" "unexpected answer for the ci runs of $sha7")"
passed="$(jq -r --arg sha "$sha" \
    'if (.workflow_runs | type) == "array"
     then any(.workflow_runs[]; .head_sha == $sha and .head_branch == "main" and .event == "push"
                                and .status == "completed" and .conclusion == "success") | tostring
     else error("shape") end' <<< "$body" 2> /dev/null)" || die "unexpected answer for the ci runs of $sha7"
[[ "$passed" == "true" ]] || die "the ci run for $sha7 has not succeeded on main"

# 4. A success status on a staging Deployment of this sha. staged_on is the earliest one.
body="$(gh_get "repos/$REPOSITORY/deployments?environment=staging&sha=$sha&per_page=100" \
    "could not read the staging deployments from GitHub" "unexpected answer for the staging deployments")"
ids="$(jq -r --arg sha "$sha" \
    'if type == "array"
     then .[] | select(.sha == $sha and .environment == "staging") | .id | tostring
     else error("shape") end' <<< "$body" 2> /dev/null)" || die "unexpected answer for the staging deployments"
successes=""
while IFS= read -r id; do
    [[ -n "$id" ]] || continue
    valid '^[0-9]+$' "$id" || die "unexpected answer for the staging deployments"
    body="$(gh_get "repos/$REPOSITORY/deployments/$id/statuses?per_page=100" \
        "could not read the statuses of a staging deployment from GitHub" \
        "unexpected answer for the staging deployment statuses")"
    dates="$(jq -r \
        'if type == "array"
         then .[] | select(.state == "success") | .created_at | strings
         else error("shape") end' <<< "$body" 2> /dev/null)" || die "unexpected answer for the staging deployment statuses"
    successes+="$dates"$'\n'
done <<< "$ids"
staged_on=""
while IFS= read -r date; do
    [[ -n "$date" ]] || continue
    valid '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$' "$date" \
        || die "unexpected answer for the staging deployment statuses"
    if [[ -z "$staged_on" || "$date" < "$staged_on" ]]; then
        staged_on="$date"
    fi
done <<< "$successes"
if [[ -z "$staged_on" ]]; then
    if [[ "$explicit" == "true" ]]; then
        die "commit $sha7 has not been rolled out to staging"
    fi
    die "main's tip $sha7 is not staged yet; wait for deploy-staging"
fi

# 5. Both images carry the full sha as a tag; read their digests from the registry. The bearer
# token goes to curl on stdin (a curl config), never on a command line.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
ACCEPT="application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json"

# registry_token <repo>: prints the token endpoint's answer for a pull of <repo>. With a login the
# request carries it as Basic credentials in a curl config on stdin; if that request fails (the
# token has no access to the package, say) it is retried once without credentials.
registry_token() {
    local url="https://ghcr.io/token?service=ghcr.io&scope=repository:$1:pull" answer
    if [[ "$registry_login" == "plain" ]] \
        && answer="$(printf 'user = "%s:%s"\n' "$REGISTRY_USER" "$REGISTRY_TOKEN" \
            | curl -sf --max-time 30 -K - "$url" 2> /dev/null)"; then
        printf '%s' "$answer"
        return 0
    fi
    # An anonymous pull token (the images are public): no credentials go to the token endpoint.
    curl -sf --max-time 30 "$url" < /dev/null 2> /dev/null
}

# registry_digest <app>: prints the manifest digest of csa-<app>:<sha>. Called only in a command
# substitution: it turns xtrace off for its own subshell, since it handles the login and the
# bearer token.
registry_digest() {
    { set +x; } 2> /dev/null
    local app="$1" token_body token code line name digest found=0
    local repo="${IMAGE_NAMESPACE#ghcr.io/}/csa-$app"
    local headers="$work/headers-$app"
    token_body="$(registry_token "$repo")" || die "could not get a registry token for csa-$app"
    token="$(jq -r '.token | strings' <<< "$token_body" 2> /dev/null)" || die "unexpected registry token answer for csa-$app"
    valid '^[A-Za-z0-9._~+/=-]+$' "$token" || die "unexpected registry token answer for csa-$app"
    code="$(printf 'header = "Authorization: Bearer %s"\n' "$token" \
        | curl -s --max-time 30 --head -K - -o /dev/null -D "$headers" -w '%{http_code}' -H "Accept: $ACCEPT" \
            "https://ghcr.io/v2/$repo/manifests/$sha" 2> /dev/null)" \
        || die "could not reach the registry for csa-$app"
    [[ "$code" != "404" ]] || die "images for $sha7 are not published (csa-$app)"
    [[ "$code" == "200" ]] || die "the registry answered HTTP ${code:0:3} for csa-$app"
    digest=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        name="${line%%:*}"
        if [[ "${name,,}" == "docker-content-digest" ]]; then
            digest="${line#*:}"
            digest="${digest#"${digest%%[![:space:]]*}"}"
            digest="${digest%"${digest##*[![:space:]]}"}"
            found=$((found + 1))
        fi
    done < "$headers"
    [[ "$found" -eq 1 ]] || die "the registry returned no single digest for csa-$app"
    valid '^sha256:[0-9a-f]{64}$' "$digest" || die "the registry returned a malformed digest for csa-$app"
    printf '%s' "$digest"
}

python_digest="$(registry_digest python)"
frontend_digest="$(registry_digest frontend)"

printf 'sha=%s\npython_digest=%s\nfrontend_digest=%s\nstaged_on=%s\nexplicit=%s\n' \
    "$sha" "$python_digest" "$frontend_digest" "$staged_on" "$explicit"
