#!/usr/bin/env bash
# Read-only repository metadata, manifest paths and explicitly selected package versions.
set -u
set -o pipefail
repo_dir="${1:-.}"
cd "$repo_dir" || exit 1
printf '## Repository directory\n'
pwd
printf '\n## Git state\n'
if command -v git >/dev/null 2>&1; then
  git rev-parse --show-toplevel 2>/dev/null || true
  git branch --show-current 2>/dev/null || true
  git rev-parse --short HEAD 2>/dev/null || true
fi
printf '\n## Manifest, route and migration paths (names only)\n'
if command -v rg >/dev/null 2>&1; then
  rg --files -g '!node_modules' -g '!.venv' -g '!venv' -g '!dist' -g '!build' \
    -g '!docs/agentic-pm/**' \
    | awk '/(^|\/)(package.json|pyproject.toml|uv.lock|requirements[^/]*\.txt|alembic.ini|Makefile|STATE.md|next.config.[^/]+|proxy.ts|docker-compose[^/]*\.ya?ml|compose[^/]*\.ya?ml)$/ || /(^|\/)(migrations|alembic|tests)\// || /(^|\/)(projects|admin_clients|client|auth)\/[^/]+\.(py|tsx|ts)$/ {print}' \
    | head -n 180 || true
else
  printf 'rg is unavailable; inspect manifest/migration paths manually.\n'
fi
printf '\n## Selected manifest dependencies\n'
if command -v python3 >/dev/null 2>&1; then
python3 - <<'MANIFEST_CHECK'
from pathlib import Path
import json, os, re
wanted={'next','react','react-dom','tailwindcss','typescript','mermaid',
        '@xyflow/react','@xterm/xterm','@xterm/addon-fit'}
blocked={'node_modules','.venv','venv','.git','.next','dist','build'}
seen=0
for directory, children, files in os.walk('.'):
    children[:]=[name for name in children if name not in blocked and not name.startswith('.')]
    for filename in files:
        if filename not in {'package.json','pyproject.toml'}: continue
        p=Path(directory)/filename
        if p.stat().st_size > 2_000_000: continue
        print(str(p))
        try:
            if filename=='package.json':
                data=json.loads(p.read_text())
                for section in ('dependencies','devDependencies'):
                    for name,value in data.get(section,{}).items():
                        if name in wanted:
                            text=str(value)
                            if re.fullmatch(r'[~^<>=*0-9a-zA-Z.+| -]+',text): print('  '+name+': '+text)
                            else: print('  '+name+': non-registry specification (not printed)')
            else:
                # Only normal package/version constraints; never print a URL/source/secret.
                for line in p.read_text().splitlines():
                    for m in re.finditer(r'\b(fastapi|uvicorn|sqlalchemy|alembic|langgraph(?:-checkpoint-postgres)?|langchain-core|psycopg)\s*([<>=!~]+[0-9][0-9a-zA-Z.,<>=!~*-]*)?',line,re.I):
                        print('  '+m.group(1)+(m.group(2) or ': present, version unspecified here'))
        except Exception as exc: print('  unable to read: '+type(exc).__name__)
        seen+=1
        if seen>=40: break
    if seen>=40: break
MANIFEST_CHECK
else
  printf 'python3 unavailable; collect selected package versions from your IDE.\n'
fi
printf '\nDo not paste .env files, database URLs, private keys, remote URLs with tokens or raw client logs.\n'
