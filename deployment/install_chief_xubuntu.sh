#!/usr/bin/env bash
# Compatibility wrapper: delegates to install_chief.sh
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$HERE/install_chief.sh" "$@"