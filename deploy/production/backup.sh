#!/bin/bash
# Nightly and pre-deploy backup of the production database to S3, installed owned by ubuntu at
# /srv/csa-prod/backup/backup.sh (install -m 0755 deploy/production/backup.sh /srv/csa-prod/backup/backup.sh).
# The bucket and region are not in this public repository: they are read, as text, from the
# instance's host file. The S3 key is read from its own file by path and never printed.
set -euo pipefail
export PATH="$PATH:/snap/bin"
LOG=/srv/csa-prod/backup/backup.log
# Never overlap, and always yield CPU and disk to the application.
exec 9>/var/tmp/csa-backup.lock
flock -n 9 || { echo "$(date -u +%Y%m%dT%H%M%SZ) skipped: previous backup still running" >> "$LOG"; exit 0; }
renice -n 19 -p $$ > /dev/null
ionice -c3 -p $$ || true
[ "$(df --output=avail -k /var/tmp | tail -1)" -gt 2097152 ] || { echo "less than 2 GB free in /var/tmp" >&2; exit 1; }
HOST_ENV=/srv/csa-prod/host.env
# host_value KEY: KEY's value from the host file, which is parsed and never sourced. A line is
# KEY=value, optionally after "export "; the value is everything after the first "=". Refuses,
# naming the key and never the value, when the key is missing, repeated, empty or not a plain
# name (letters, digits, dot, underscore, hyphen).
host_value() {
    local key="$1" lines value
    [ -r "$HOST_ENV" ] || { echo "cannot read $HOST_ENV" >&2; return 1; }
    lines=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$HOST_ENV" || true)
    [ -n "$lines" ] || { echo "$key is missing from $HOST_ENV" >&2; return 1; }
    [[ "$lines" != *$'\n'* ]] || { echo "$key is set more than once in $HOST_ENV" >&2; return 1; }
    value=$(printf '%s\n' "$lines" | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    [ -n "$value" ] || { echo "$key is empty in $HOST_ENV" >&2; return 1; }
    # The C locale keeps the ranges ASCII: a UTF-8 locale lets lookalike letters and digits in.
    printf '%s\n' "$value" | LC_ALL=C grep -Eqx '[A-Za-z0-9._-]+' \
        || { echo "$key in $HOST_ENV is not a plain name" >&2; return 1; }
    printf '%s\n' "$value"
}
B=$(host_value CSA_PROD_S3_BUCKET)
REGION=$(host_value CSA_PROD_S3_REGION)
read -r AK SK < /srv/csa-prod/backup/s3-key.txt
export AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_DEFAULT_REGION="$REGION"
ts=$(date -u +%Y%m%dT%H%M%SZ)
tmp=$(mktemp /var/tmp/csa-dump.XXXXXX); trap 'rm -f "$tmp"' EXIT
docker exec csa-prod-staging-db-1 nice -n 19 pg_dump -U csa -d csa_staging -Fc -Z 3 > "$tmp"
docker exec -i csa-prod-staging-db-1 pg_restore --list < "$tmp" > /dev/null
aws s3 cp "$tmp" "s3://$B/backups/csa_staging-$ts.dump" --only-show-errors
[ -s /srv/csa-prod/backup/ping-url ] && curl -fsS -m 10 "$(cat /srv/csa-prod/backup/ping-url)" > /dev/null || true
echo "$ts ok $(stat -c %s "$tmp") bytes" >> "$LOG"
