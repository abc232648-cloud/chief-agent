#!/usr/bin/env bash
set -euo pipefail

# The implementation remains a readable shell runbook, while this launcher injects
# host/runtime details that must be established around that runbook: the future
# tooling import path, Chief-owned Playwright storage and Node's future PATH. Older
# implementations receive persistent user-service startup here; newer readable
# runbooks carry that step directly. The launcher also removes NoNewPrivileges from
# the Caddy user unit because Caddy intentionally receives only CAP_NET_BIND_SERVICE
# on its verified executable.
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE="$HERE/install_chief_xubuntu.sh"
[[ -f "$SOURCE" ]] || { echo "Installer implementation is missing." >&2; exit 1; }
TEMP="$(mktemp "${TMPDIR:-/tmp}/chief-first-install.XXXXXX.sh")"
trap 'rm -f "$TEMP"' EXIT

python3 - "$SOURCE" "$TEMP" <<'PY'
from pathlib import Path
import sys
src,dst=map(Path,sys.argv[1:])
text=src.read_text(encoding='utf-8')
anchor='umask 077\n'
if text.count(anchor)!=1:raise SystemExit('Installer umask anchor changed; review launcher integration.')
text=text.replace(anchor,anchor+'export PYTHONPATH="$HOME/.local/share/chief/tooling${PYTHONPATH:+:$PYTHONPATH}"\nexport PLAYWRIGHT_BROWSERS_PATH="$HOME/.local/share/chief/browsers"\n',1)
anchor='sudo -v\n'
if text.count(anchor)!=1:raise SystemExit('Installer sudo anchor changed; review launcher integration.')
if 'sudo loginctl enable-linger "$(id -un)"' not in text:
    text=text.replace(anchor,anchor+'sudo loginctl enable-linger "$(id -un)"\n',1)
anchor='if [[ "${farm_answer:-N}" =~ ^[Yy]$ ]]; then FARM_OPERATIONS=ENABLED; else FARM_OPERATIONS=DISABLED; fi\n'
if text.count(anchor)!=1:raise SystemExit('Installer Farm prompt anchor changed; review ownership bootstrap integration.')
text=text.replace(anchor,anchor+'''OWNERSHIP_ARGS=()\nif [[ "$FARM_OPERATIONS" == ENABLED ]]; then\n  read -r -p "Farm-owning organisation/company [${CHIEF_OWNER_NAME}]: " FARM_ORGANIZATION_NAME\n  FARM_ORGANIZATION_NAME="${FARM_ORGANIZATION_NAME:-$CHIEF_OWNER_NAME}"\n  while :; do\n    read -r -p "First farm name: " FIRST_FARM_NAME\n    [[ -n "${FIRST_FARM_NAME//[[:space:]]/}" ]] && break\n    echo "Enter the farm name Chief should use."\n  done\n  OWNERSHIP_ARGS=(--organization-name "$FARM_ORGANIZATION_NAME" --farm-name "$FIRST_FARM_NAME")\nfi\n''',1)
anchor='  --farm-operations "$FARM_OPERATIONS"\n'
if text.count(anchor)!=1:raise SystemExit('Installer prepare-production Farm argument anchor changed; review ownership bootstrap integration.')
text=text.replace(anchor,'  --farm-operations "$FARM_OPERATIONS" \\\n  "${OWNERSHIP_ARGS[@]}"\n',1)
anchor='NODE="$NODE_ROOT/bin/node"; NPM="$NODE_ROOT/bin/npm"\n'
if text.count(anchor)!=1:raise SystemExit('Installer Node anchor changed; review launcher integration.')
text=text.replace(anchor,anchor+'export PATH="$NODE_ROOT/bin:$PATH"\n',1)
anchor='NoNewPrivileges=yes\nUMask=0077\n'
if text.count(anchor)!=1:raise SystemExit('Installer Caddy service anchor changed; review launcher integration.')
text=text.replace(anchor,'UMask=0077\n',1)
dst.write_text(text,encoding='utf-8')
PY
chmod 700 "$TEMP"
exec bash "$TEMP"
