#!/bin/bash
# Creates the alert topic, eight CloudWatch alarms, the dashboard and three Lightsail alarms.
# Run from a machine with AWS admin access:  create_alarms.sh PROFILE you@example.com
# Safe to run again: topics, alarms and the dashboard are overwritten with the same names.
set -euo pipefail
PROFILE=${1:?AWS profile name}
EMAIL=${2:?alert email address}
REGION=ap-southeast-1
HERE=$(cd "$(dirname "$0")" && pwd)
aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

TOPIC=$(aws_ sns create-topic --name csa-prod-alerts --query TopicArn --output text)
aws_ sns subscribe --topic-arn "$TOPIC" --protocol email --notification-endpoint "$EMAIL" > /dev/null
echo "Topic $TOPIC. Confirm the email AWS sent to $EMAIL, or no alert will arrive."

# alarm NAME METRIC STATISTIC OPERATOR THRESHOLD EVALUATIONS DATAPOINTS MISSING DESCRIPTION
alarm() {
  aws_ cloudwatch put-metric-alarm --alarm-name "csa-prod-$1" --namespace CSA/Prod \
    --metric-name "$2" --statistic "$3" --comparison-operator "$4" --threshold "$5" \
    --period 300 --evaluation-periods "$6" --datapoints-to-alarm "$7" \
    --treat-missing-data "$8" --alarm-description "$9" \
    --alarm-actions "$TOPIC" --ok-actions "$TOPIC"
}
alarm console-down-or-silent ConsoleUp Minimum LessThanThreshold 1 3 3 breaching \
  "The console did not answer, or the instance stopped reporting (15 minutes)."
alarm database-down DbUp Minimum LessThanThreshold 1 2 2 breaching \
  "Postgres is not accepting connections."
alarm backup-stale BackupAgeMinutes Maximum GreaterThanThreshold 450 1 1 breaching \
  "No successful backup for over 7.5 hours (every 6 hours is expected)."
alarm memory-low MemAvailableMB Minimum LessThanThreshold 120 3 2 notBreaching \
  "Less than 120 MB of memory available."
alarm swap-high SwapUsedMB Maximum GreaterThanThreshold 1700 2 2 notBreaching \
  "Swap is almost full."
alarm oom-kill OOMEvents Maximum GreaterThanOrEqualToThreshold 1 1 1 notBreaching \
  "The kernel killed a process for lack of memory."
alarm queue-backlog QueueDepth Maximum GreaterThanThreshold 150 3 3 notBreaching \
  "More than 150 customer messages waiting for 15 minutes."
alarm disk-high DiskUsedPercent Maximum GreaterThanThreshold 80 2 2 notBreaching \
  "The instance disk is over 80% full."

ACCOUNT=$(aws_ sts get-caller-identity --query Account --output text)
BODY=$(sed "s/:ACCOUNT:/:$ACCOUNT:/g" "$HERE/dashboard.json")
aws_ cloudwatch put-dashboard --dashboard-name csa-prod --dashboard-body "$BODY" > /dev/null
echo "Dashboard csa-prod created."

# Lightsail's own alarms (no key on the instance). Needs a verified email contact method.
aws_ lightsail create-contact-method --protocol Email --contact-endpoint "$EMAIL" > /dev/null 2>&1 || true
ls_alarm() {
  aws_ lightsail put-alarm --alarm-name "$1" --metric-name "$2" --monitored-resource-name csa-prod \
    --comparison-operator "$3" --threshold "$4" --evaluation-periods "$5" \
    --contact-protocols Email --notification-triggers ALARM --notification-enabled > /dev/null
}
ls_alarm csa-prod-status-check StatusCheckFailed GreaterThanOrEqualToThreshold 1 2
ls_alarm csa-prod-cpu-high CPUUtilization GreaterThanOrEqualToThreshold 85 3
ls_alarm csa-prod-burst-low BurstCapacityPercentage LessThanOrEqualToThreshold 20 2
echo "Lightsail alarms set. Confirm the Lightsail verification email too."
