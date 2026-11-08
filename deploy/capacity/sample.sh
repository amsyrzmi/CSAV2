#!/bin/bash
# Host-side sampler for the capacity gate. Every INTERVAL seconds it appends one block to the
# output file: memory and swap, per-container memory and CPU, CPU steal (a burstable instance
# whose credits run out shows high steal), memory and CPU pressure, OOM kills and restarts.
# Cheap by design (a few reads); run it at low priority. Stop it with Ctrl+C or kill.
#
#   nohup nice -n 19 ./sample.sh /var/tmp/capacity-samples.log 15 >/dev/null 2>&1 &
set -uo pipefail
OUT=${1:-/var/tmp/capacity-samples.log}
INTERVAL=${2:-15}
PROJECT=${CSA_STAGING_PROJECT:-csa-prod}
while true; do
  {
    echo "=== $(date -u +%FT%TZ)"
    free -m | awk 'NR==2{print "mem_total="$2" used="$3" available="$7} NR==3{print "swap_used="$3}'
    vmstat 1 2 | tail -1 | awk '{print "cpu_user="$13" system="$14" idle="$15" iowait="$16" steal="$17}'
    for f in memory cpu; do
      [ -r /proc/pressure/$f ] && echo "pressure_$f: $(head -1 /proc/pressure/$f)"
    done
    docker stats --no-stream --format '{{.Name}} mem={{.MemUsage}} cpu={{.CPUPerc}}' 2>/dev/null
    for c in $(docker ps -a --filter "name=${PROJECT}-" --format '{{.Names}}'); do
      docker inspect -f '{{.Name}} oom_killed={{.State.OOMKilled}} restarts={{.RestartCount}} status={{.State.Status}}' "$c"
    done
  } >> "$OUT"
  sleep "$INTERVAL"
done
