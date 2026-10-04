#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
project_dir="$PWD"
if [[ "$project_dir" == *$'\n'* || "$project_dir" == *'"'* || "$project_dir" == *'%'* ]]; then
  echo 'Project path contains characters unsupported by the service installer.' >&2
  exit 1
fi
test -x "$project_dir/.venv/bin/python"
mkdir -p "$HOME/.config/systemd/user"
for component in dashboard worker; do
  if [[ "$component" == dashboard ]]; then
    arguments='-u dashboard_app.py'
  else
    arguments='-u -m worker.runner'
  fi
  unit_path="$HOME/.config/systemd/user/chief-agent-${component}.service"
  if [[ -f "$unit_path" ]]; then cp -p -- "$unit_path" "$unit_path.before-install-$(date +%Y%m%d-%H%M%S)"; fi
  cat > "$unit_path" <<EOF
[Unit]
Description=Chief Agent $component
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$project_dir
ExecStart="$project_dir/.venv/bin/python" $arguments
Restart=on-failure
RestartSec=10
UMask=0077

[Install]
WantedBy=default.target
EOF
done
systemctl --user daemon-reload
systemd-analyze --user verify "$HOME/.config/systemd/user/chief-agent-dashboard.service" "$HOME/.config/systemd/user/chief-agent-worker.service"
systemctl --user enable chief-agent-dashboard.service chief-agent-worker.service
echo 'Service files installed and enabled. Stop any manually started copies before starting these services.'
echo "To start before desktop login after reboot, an administrator must run: sudo loginctl enable-linger $(id -un)"
echo 'Start: systemctl --user start chief-agent-dashboard chief-agent-worker'
echo 'Status: systemctl --user status chief-agent-dashboard chief-agent-worker'
echo 'Logs: journalctl --user -u chief-agent-dashboard -u chief-agent-worker -n 100'
