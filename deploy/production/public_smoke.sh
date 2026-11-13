#!/bin/bash
# The last step of a production deploy: ask the public console origin for / and read only the
# HTTP status.
#   public_smoke.sh <origin>      origin like https://host (no path, port or trailing slash)
# Exit 0 only for a 2xx or 3xx status (a login redirect is success and is not followed); a
# malformed origin, a curl failure and any other status exit non-zero. The repository is public,
# so nothing here ever prints the origin: diagnostics give the status only, curl's own stderr is
# discarded, and the origin reaches curl as a config on stdin, not on a command line.
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

[[ "$#" -eq 1 ]] || die "usage: public_smoke.sh <origin>"
origin="$1"
valid '^https://[A-Za-z0-9.-]+$' "$origin" || die "the origin must look like https://host"

# --retry covers only transient failures (timeouts, 5xx-class retryable answers); a plain 4xx
# ends at once. The status alone is read; the body is dropped.
code="$(printf 'url = "%s/"\n' "$origin" \
    | curl -s --max-time 15 --retry 3 --retry-delay 5 -K - -o /dev/null -w '%{http_code}' 2> /dev/null)" \
    || die "the console could not be reached"
valid '^[0-9]{3}$' "$code" || die "the console answered with an unreadable status"
[[ "$code" == 2* || "$code" == 3* ]] || die "the console did not answer with 2xx or 3xx (status $code)"
echo "the console answered (status $code)"
