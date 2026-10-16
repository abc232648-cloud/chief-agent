#!/usr/bin/env bash
set -euo pipefail

# Chief Unified Installation Compatibility Entrypoint.
# The installer implementation has been extracted to the dedicated repository:
#   https://github.com/abc232648-cloud/chief-installer
#
# This script delegates execution to the authoritative unified installer.

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
AGENT_ROOT="$(cd "$HERE/.." && pwd)"

# 1. If chief-installer binary is in PATH, execute it
if command -v chief-installer >/dev/null 2>&1; then
    exec chief-installer "$@"
fi

# 2. If a local checkout of chief-installer exists alongside chief-agent, delegate to it
INSTALLER_REPO="$(cd "$AGENT_ROOT/../chief-installer" 2>/dev/null && pwd || true)"
if [[ -n "$INSTALLER_REPO" && -d "$INSTALLER_REPO/deployment/installer" ]]; then
    PYTHONPATH="$INSTALLER_REPO${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m deployment.installer.cli "$@"
fi

# 3. If python module chief_installer is installed, execute it
if python3 -c "import chief_installer" >/dev/null 2>&1; then
    exec python3 -m chief_installer.cli "$@"
fi

echo "Chief installation is managed by the dedicated Chief Unified Installer." >&2
echo "Please download the installer package or clone from:" >&2
echo "  https://github.com/abc232648-cloud/chief-installer" >&2
exit 1
