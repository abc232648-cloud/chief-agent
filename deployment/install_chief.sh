#!/usr/bin/env bash
set -euo pipefail

# The implementation remains a readable shell runbook, while this launcher injects
# host/runtime details that must be established around that runbook: the future
# tooling import path, Chief-owned Playwright storage, Node's future PATH, and
# persistent user-service startup across reboots. It also removes NoNewPrivileges
# from the Caddy user unit because Caddy intentionally receives only
# CAP_NET_BIND_SERVICE on its verified executable.
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
text=text.replace(anchor,anchor+'sudo loginctl enable-linger "$(id -un)"\n',1)
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
