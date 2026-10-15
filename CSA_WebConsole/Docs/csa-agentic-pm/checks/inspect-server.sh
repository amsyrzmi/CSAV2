#!/usr/bin/env bash
# Read-only Linux server inventory. No sudo, env/config contents, logs or process args.
set -u
set -o pipefail
section() { printf '\n## %s\n' "$1"; }
section 'Host and OS'
hostname 2>/dev/null || true
uname -srm 2>/dev/null || true
if [ -r /etc/os-release ]; then
  awk -F= '$1 == "PRETTY_NAME" {print $0}' /etc/os-release
fi
section 'CPU, memory and filesystem capacity'
getconf _NPROCESSORS_ONLN 2>/dev/null || true
command -v free >/dev/null 2>&1 && free -h || true
df -h / 2>/dev/null || true
section 'Installed command locations'
for cmd in docker python3 uv node npm psql tailscale hermes; do
  command -v "$cmd" 2>/dev/null || true
done
section 'Tool versions'
python3 --version 2>/dev/null || true
node --version 2>/dev/null || true
npm --version 2>/dev/null || true
psql --version 2>/dev/null || true
if command -v docker >/dev/null 2>&1; then
  docker --version 2>/dev/null || true
  docker compose version 2>/dev/null || true
fi
section 'Running containers (metadata only)'
if command -v docker >/dev/null 2>&1; then
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null \
    || printf 'Docker daemon inventory unavailable to this user.\n'
fi
section 'Listening TCP addresses (no process command lines)'
if command -v ss >/dev/null 2>&1; then ss -ltnH 2>/dev/null || true; fi
section 'Related service names (no unit contents or logs)'
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --all --type=service --no-legend --no-pager 2>/dev/null \
    | awk 'tolower($0) ~ /(hermes|csa|console|postgres|n8n|langgraph|tailscale)/ {print $0}' || true
fi
section 'Python packages in this interpreter only'
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PACKAGE_CHECK'
from importlib import metadata
for name in ('fastapi','uvicorn','sqlalchemy','alembic','langgraph',
             'langgraph-checkpoint-postgres','langchain-core','psycopg','hermes-agent'):
    try: print(name + ': ' + metadata.version(name))
    except metadata.PackageNotFoundError: print(name + ': not in this interpreter')
PACKAGE_CHECK
fi
section 'Hermes help (if installed; no agent run)'
if command -v hermes >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  timeout 10s hermes --help 2>&1 || printf 'Help unavailable or timed out; inspect installation privately.\n'
else
  printf 'Hermes or timeout unavailable. Collect installed CLI help separately.\n'
fi
section 'Notes'
printf '%s\n' 'Missing packages may live in another venv/container.' \
  'Review hostnames, IPs and paths before sharing.' \
  'No environment values, private memories, config bodies or customer rows were requested.'
