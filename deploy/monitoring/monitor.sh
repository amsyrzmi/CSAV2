#!/bin/bash
# Pushes eight health numbers to CloudWatch (namespace CSA/Prod). Run every 5 minutes from cron.
# Needs a key limited to cloudwatch:PutMetricData in that namespace, in MONITOR_KEY ("id secret",
# one line). The alarms treat a missing number as a failure, so a dead or cut-off instance alerts.
# DRY_RUN=1 prints the numbers instead of sending them.
set -uo pipefail
export PATH="$PATH:/snap/bin"
renice -n 19 -p $$ > /dev/null 2>&1
ionice -c3 -p $$ > /dev/null 2>&1 || true
KEY=${MONITOR_KEY:-/srv/csa-prod/monitor/monitor-key.txt}
PROJECT=${CSA_STAGING_PROJECT:-csa-prod}
URL=${MONITOR_URL:-https://mito.fourstacks.my/}
BACKUP_LOG=${BACKUP_LOG:-/srv/csa-prod/backup/backup.log}

mem_available=$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo)
swap_used=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{printf "%d", (t-f)/1024}' /proc/meminfo)
disk_used=$(df --output=pcent / | tail -1 | tr -dc '0-9')

code=$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$URL" || true)
case "$code" in 200|301|302|307|308) console_up=1 ;; *) console_up=0 ;; esac

if docker exec "$PROJECT-staging-db-1" pg_isready -q -U csa -d csa_staging 2> /dev/null; then
  db_up=1
  queue=$(docker exec "$PROJECT-staging-db-1" psql -U csa -d csa_staging -tA \
    -c "SELECT count(*) FROM ingress_events WHERE status IN ('received','dispatched')" 2> /dev/null)
  queue=${queue:-0}
else
  db_up=0
  queue=0
fi

# Kernel OOM kills in the last 6 minutes (cadence is 5, so none is missed).
oom=$(journalctl -k --since '-6 min' -q 2> /dev/null | grep -ciE 'out of memory|oom-kill' || true)
oom=${oom:-0}

last=$(grep ' ok ' "$BACKUP_LOG" 2> /dev/null | tail -1 | cut -d' ' -f1)
if [ -n "$last" ]; then
  then_s=$(date -u -d "${last:0:4}-${last:4:2}-${last:6:2} ${last:9:2}:${last:11:2}:${last:13:2}" +%s 2> /dev/null || echo 0)
  backup_age=$(( ($(date -u +%s) - then_s) / 60 ))
else
  backup_age=99999
fi

DATA=(
  "MetricName=ConsoleUp,Value=$console_up,Unit=Count"
  "MetricName=DbUp,Value=$db_up,Unit=Count"
  "MetricName=MemAvailableMB,Value=$mem_available,Unit=Megabytes"
  "MetricName=SwapUsedMB,Value=$swap_used,Unit=Megabytes"
  "MetricName=OOMEvents,Value=$oom,Unit=Count"
  "MetricName=QueueDepth,Value=$queue,Unit=Count"
  "MetricName=BackupAgeMinutes,Value=$backup_age,Unit=None"
  "MetricName=DiskUsedPercent,Value=$disk_used,Unit=Percent"
)
if [ "${DRY_RUN:-0}" = 1 ]; then
  printf '%s\n' "${DATA[@]}"
  exit 0
fi
read -r AK SK < "$KEY"
export AWS_ACCESS_KEY_ID="$AK" AWS_SECRET_ACCESS_KEY="$SK" AWS_DEFAULT_REGION=ap-southeast-1
aws cloudwatch put-metric-data --namespace CSA/Prod --metric-data "${DATA[@]}"
