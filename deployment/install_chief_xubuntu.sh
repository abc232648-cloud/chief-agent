#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_URL="${CHIEF_REPO_URL:-https://github.com/abc232648-cloud/chief-agent.git}"
OWNER_REPO="https://github.com/abc232648-cloud/chief-owner-pwa.git"
STAFF_REPO="https://github.com/abc232648-cloud/chief-staff-pwa.git"
JOBS_REPO="https://github.com/abc232648-cloud/Job-agent-pwa.git"
RELEASE_REF="chief-install-closure-2026-10-14"
RELEASE_COMMIT="0d96fdf979b9eba0f3deb0024278f5c2557a5319"
NODE_VERSION="22.23.3"
CADDY_VERSION="2.11.7"
CADDY_CHECKSUMS_SHA256="5d27b76b95f496638d79208c55250317b8755fd9e90005f7466aa32956944a90"

ROOT="$HOME/.local/share/chief"
TOOLING="$ROOT/tooling"
RELEASE_SOURCE="$ROOT/release-source"
RUNTIME="$ROOT/runtime"
PYTHON="$RUNTIME/bin/python"
NODE_ROOT="$ROOT/node"
PWA_ROOT="$ROOT/pwas"
MANAGED="$ROOT/managed"
PRODUCTION="$ROOT/production"
CADDY_ROOT="$ROOT/caddy"
CONFIG_ROOT="$HOME/.config/chief"
CONFIG="$CONFIG_ROOT/production-instance.json"
IDENTITY_CONFIG="$CONFIG_ROOT/installation-identity.json"
BROWSER_CONFIG="$CONFIG_ROOT/browser-runtime.json"
CADDYFILE="$CONFIG_ROOT/Caddyfile"
STATE="$HOME/.local/state/chief"
META="$STATE/release-metadata"
SOURCE_MANIFEST="$META/SOURCE_MANIFEST.json"
BACKUP="$STATE/backups/initial"
UNIT_STAGE="$STATE/service-units/initial"
UNIT_DEST="$HOME/.config/systemd/user"
LOG="$HOME/chief-first-install.log"

exec > >(tee -a "$LOG") 2>&1

die(){ echo "ERROR: $*" >&2; exit 1; }
command -v bash >/dev/null || die "bash is required"
[[ "$(uname -s)" == "Linux" ]] || die "This installer targets Linux/Xubuntu."
command -v apt-get >/dev/null || die "This installer currently requires an apt-based Xubuntu/Ubuntu host."

printf '\nChief first installation\n========================\n'
echo "This run will archive previous Chief deployments, install required system packages,"
echo "create a fresh private runtime and database, build the three PWAs, install local discovery/TLS,"
echo "install user services, and start Chief."
printf '\n'
read -r -p "Continue with a fresh Chief installation? [Y/n] " answer
[[ "${answer:-Y}" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

while :; do
  read -r -p "Who is this Chief for? (your name or organisation name): " CHIEF_OWNER_NAME
  [[ -n "${CHIEF_OWNER_NAME//[[:space:]]/}" ]] && break
  echo "Enter the person or organisation Chief belongs to."
done
read -r -p "Owner username [owner]: " OWNER_USERNAME
OWNER_USERNAME="${OWNER_USERNAME:-owner}"
read -r -p "Enable production Farm operations now? [y/N] " farm_answer
if [[ "${farm_answer:-N}" =~ ^[Yy]$ ]]; then FARM_OPERATIONS=ENABLED; else FARM_OPERATIONS=DISABLED; fi

# Normal first-run installations are local and require no DNS knowledge. Public DNS
# remains an administrator-only override for deployments that already control DNS.
if [[ -n "${CHIEF_PUBLIC_HOST:-}" ]]; then TLS_MODE=PUBLIC; else TLS_MODE=LOCAL; fi

STAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_BASE="$HOME/Chief_Previous_Installations"
ARCHIVE="$ARCHIVE_BASE/$STAMP"
mkdir -p "$ARCHIVE"/{home,state,share,config,systemd,system,logs,repositories,inventory}
chmod 700 "$ARCHIVE_BASE" "$ARCHIVE"

echo "Stopping any previous Chief user services..."
systemctl --user disable --now chief-agent-dashboard.service chief-agent-worker.service chief-agent-scheduler.service chief-tls-proxy.service >/dev/null 2>&1 || true

if [[ -d "$HOME/chief-agent/.git" ]]; then
  git -C "$HOME/chief-agent" worktree list --porcelain > "$ARCHIVE/inventory/old-worktrees.txt" 2>/dev/null || true
  git -C "$HOME/chief-agent" bundle create "$ARCHIVE/repositories/chief-agent.bundle" --all >/dev/null 2>&1 || true
fi

: > "$ARCHIVE/MOVED_PATHS.txt"
move_path(){
  local src="$1" bucket="$2" base dest
  [[ -e "$src" || -L "$src" ]] || return 0
  [[ "$src" == "$ARCHIVE_BASE" || "$src" == "$ARCHIVE_BASE/"* ]] && return 0
  base="$(basename "$src")"; dest="$ARCHIVE/$bucket/$base"
  [[ ! -e "$dest" && ! -L "$dest" ]] || dest="$ARCHIVE/$bucket/${base}__${STAMP}"
  echo "Archiving: $src"
  mv -- "$src" "$dest"
  printf '%s\t%s\n' "$src" "$dest" >> "$ARCHIVE/MOVED_PATHS.txt"
}

# Known legacy project/runtime/release paths.
for p in "$HOME/chief-agent" "$HOME/chief-runtime" "$HOME"/chief-release "$HOME"/chief-release-* "$HOME"/chief-preview "$HOME"/chief-preview-* "$HOME"/chief-staging "$HOME"/chief-staging-* "$HOME"/chief-test "$HOME"/chief-test-*; do move_path "$p" home; done
# Current and historical XDG Chief roots. This intentionally archives production too: a fresh install must not borrow old state.
for p in "$HOME"/.local/state/chief "$HOME"/.local/state/chief-* "$HOME"/.local/state/chief_*; do move_path "$p" state; done
for p in "$HOME"/.local/share/chief "$HOME"/.local/share/chief-* "$HOME"/.local/share/chief_*; do move_path "$p" share; done
for p in "$HOME"/.config/chief "$HOME"/.config/chief-* "$HOME"/.config/chief_*; do move_path "$p" config; done
for p in "$HOME"/chief-deploy*.log "$HOME"/chief-install*.log "$HOME"/chief-first-install*.log; do [[ "$p" == "$LOG" ]] || move_path "$p" logs; done

if [[ -d "$UNIT_DEST" ]]; then
  while IFS= read -r -d '' p; do move_path "$p" systemd; done < <(find "$UNIT_DEST" -maxdepth 1 \( -name 'chief-agent-*.service' -o -name 'chief-tls-proxy.service' -o -name 'chief-agent-*.timer' \) -print0 2>/dev/null)
fi
systemctl --user daemon-reload >/dev/null 2>&1 || true

{
  echo "Archived at $(date --iso-8601=seconds)"
  echo "Nothing above was deleted; paths were moved into this directory."
  echo
  cat "$ARCHIVE/MOVED_PATHS.txt"
} > "$ARCHIVE/ARCHIVE_SUMMARY.txt"

echo "Previous Chief material archived under: $ARCHIVE"

echo "Requesting one administrator authorization for operating-system dependencies..."
sudo -v
sudo loginctl enable-linger "$(id -un)" >/dev/null
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git curl ca-certificates python3 python3-venv python3-pip xz-utils libcap2-bin \
  avahi-daemon avahi-utils libnss-mdns iproute2

mkdir -p "$ROOT" "$CONFIG_ROOT" "$STATE" "$META" "$UNIT_DEST"
chmod 700 "$ROOT" "$CONFIG_ROOT" "$STATE" "$META" "$UNIT_DEST"

echo "Cloning fresh Chief tooling..."
git clone --no-local "$REPO_URL" "$TOOLING"
git -C "$TOOLING" fetch --tags origin
git -C "$TOOLING" worktree add --detach "$RELEASE_SOURCE" "$RELEASE_REF"
[[ "$(git -C "$RELEASE_SOURCE" rev-parse HEAD)" == "$RELEASE_COMMIT" ]] || die "Release tag resolved to an unexpected commit."
[[ -z "$(git -C "$RELEASE_SOURCE" status --porcelain --untracked-files=no)" ]] || die "Release source is not clean."

if [[ "$TLS_MODE" == LOCAL ]]; then
  BASE_LABEL="$(PYTHONPATH="$TOOLING${PYTHONPATH:+:$PYTHONPATH}" python3 -c 'import sys;from deployment.installation_identity import local_host_label;print(local_host_label(sys.argv[1]))' "$CHIEF_OWNER_NAME")"
  LOCAL_LABEL="$BASE_LABEL"
  collision=2
  while :; do
    RESOLVED_IP="$(getent ahostsv4 "${LOCAL_LABEL}.local" 2>/dev/null | awk 'NR==1{print $1}')"
    [[ -z "$RESOLVED_IP" ]] && break
    if ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$RESOLVED_IP"; then break; fi
    suffix="-${collision}"
    max_stem=$((63-${#suffix}))
    stem="${BASE_LABEL:0:${max_stem}}"; stem="${stem%-}"
    LOCAL_LABEL="${stem}${suffix}"
    collision=$((collision+1))
  done
  PUBLIC_HOST="${LOCAL_LABEL}.local"

  if [[ -f /etc/avahi/avahi-daemon.conf ]]; then
    sudo cp -a /etc/avahi/avahi-daemon.conf "$ARCHIVE/system/avahi-daemon.conf.before-chief"
  fi
  sudo python3 - "$LOCAL_LABEL" <<'PY'
from pathlib import Path
import os,sys,tempfile
label=sys.argv[1]
path=Path('/etc/avahi/avahi-daemon.conf')
text=path.read_text(encoding='utf-8') if path.exists() else ''
lines=text.splitlines(keepends=True)
out=[]; in_server=False; inserted=False; seen_server=False
for line in lines:
    stripped=line.strip()
    if stripped.startswith('[') and stripped.endswith(']'):
        if in_server and not inserted:
            out.append(f'host-name={label}\n'); inserted=True
        in_server=stripped.lower()=='[server]'
        seen_server=seen_server or in_server
    if in_server and stripped.lower().startswith('host-name='):
        if not inserted:out.append(f'host-name={label}\n');inserted=True
        continue
    out.append(line)
if in_server and not inserted:
    out.append(f'host-name={label}\n');inserted=True
if not seen_server:
    out=['[server]\n',f'host-name={label}\n','\n']+out
payload=''.join(out)
fd,tmp=tempfile.mkstemp(prefix='.avahi-daemon.conf.',dir=str(path.parent))
try:
    with os.fdopen(fd,'w',encoding='utf-8') as handle:handle.write(payload)
    if path.exists():os.chmod(tmp,path.stat().st_mode & 0o777)
    else:os.chmod(tmp,0o644)
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
PY
  sudo systemctl enable avahi-daemon >/dev/null
  sudo systemctl restart avahi-daemon

  resolved_local=0
  for _ in {1..10}; do
    RESOLVED_IP="$(getent ahostsv4 "$PUBLIC_HOST" 2>/dev/null | awk 'NR==1{print $1}')"
    if [[ -n "$RESOLVED_IP" ]] && ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$RESOLVED_IP"; then resolved_local=1; break; fi
    sleep 1
  done
  [[ "$resolved_local" == 1 ]] || die "Chief local hostname was not advertised by this machine."
  PUBLIC_HOST="$(PYTHONPATH="$TOOLING${PYTHONPATH:+:$PYTHONPATH}" python3 -m deployment.installation_identity --owner-name "$CHIEF_OWNER_NAME" --output "$IDENTITY_CONFIG" --local-host "$PUBLIC_HOST")"
else
  PUBLIC_HOST="$(PYTHONPATH="$TOOLING${PYTHONPATH:+:$PYTHONPATH}" python3 -m deployment.installation_identity --owner-name "$CHIEF_OWNER_NAME" --output "$IDENTITY_CONFIG" --public-host "$CHIEF_PUBLIC_HOST")"
fi

echo "Chief address selected automatically: https://${PUBLIC_HOST}/"

python3 -m venv "$RUNTIME"
"$PYTHON" -m pip install --upgrade pip setuptools wheel
"$PYTHON" -m pip install -r "$RELEASE_SOURCE/requirements.txt"
"$PYTHON" -m pip check
"$PYTHON" -m pip freeze > "$STATE/python-runtime-freeze.txt"

echo "Installing Chromium and its native Linux dependencies..."
"$PYTHON" -m playwright install --with-deps chromium
BROWSER_EXE="$("$PYTHON" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
PY
)"
[[ -x "$BROWSER_EXE" ]] || die "Playwright Chromium executable was not installed."
BROWSER_SHA="$(sha256sum "$BROWSER_EXE" | awk '{print $1}')"
BROWSER_VERSION="$("$BROWSER_EXE" --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
[[ "$BROWSER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Could not determine Chromium version."
"$PYTHON" - "$BROWSER_CONFIG" "$BROWSER_EXE" "$BROWSER_SHA" "$BROWSER_VERSION" <<'PY'
import json,os,sys
path,exe,digest,version=sys.argv[1:]
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w',encoding='utf-8') as f:
    json.dump({'executable':exe,'sha256':digest,'version':version},f,sort_keys=True);f.write('\n')
PY

echo "Installing a private Node.js runtime for PWA builds..."
case "$(uname -m)" in x86_64) NODE_ARCH=x64;; aarch64|arm64) NODE_ARCH=arm64;; *) die "Unsupported CPU architecture for bundled Node.js.";; esac
NODE_FILE="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
NODE_TMP="$STATE/node-download"
mkdir -p "$NODE_TMP" "$NODE_ROOT"
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$NODE_TMP/SHASUMS256.txt"
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_FILE}" -o "$NODE_TMP/$NODE_FILE"
( cd "$NODE_TMP" && grep "  ${NODE_FILE}$" SHASUMS256.txt | sha256sum -c - )
tar -xJf "$NODE_TMP/$NODE_FILE" -C "$NODE_ROOT" --strip-components=1
NODE="$NODE_ROOT/bin/node"; NPM="$NODE_ROOT/bin/npm"
"$NODE" --version; "$NPM" --version

build_pwa(){
  local name="$1" repo="$2" dir="$PWA_ROOT/$name"
  echo "Building $name PWA from the published release tag..."
  git clone --branch "$RELEASE_REF" --depth 1 "$repo" "$dir"
  ( cd "$dir" && "$NPM" ci && "$NPM" run build )
  [[ -f "$dir/dist/index.html" ]] || die "$name PWA build did not produce dist/index.html"
}
mkdir -p "$PWA_ROOT"
build_pwa owner "$OWNER_REPO"
build_pwa staff "$STAFF_REPO"
build_pwa jobs "$JOBS_REPO"

"$PYTHON" -m deployment build-source-manifest \
  --source-root "$RELEASE_SOURCE" --output "$SOURCE_MANIFEST" --expected-git-commit "$RELEASE_COMMIT"
EXPECTED_SOURCE="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source_tree_sha256"])' "$SOURCE_MANIFEST")"

echo "Preparing a brand-new production database. The password is entered only into Chief's password prompt."
"$PYTHON" -m deployment prepare-production \
  --directory "$PRODUCTION" \
  --config "$CONFIG" \
  --source-root "$RELEASE_SOURCE" \
  --source-manifest "$SOURCE_MANIFEST" \
  --username "$OWNER_USERNAME" \
  --public-host "$PUBLIC_HOST" \
  --browser-runtime "$BROWSER_CONFIG" \
  --farm-operations "$FARM_OPERATIONS"
EXPECTED_SCHEMA="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["schema_sha256"])' "$CONFIG")"

"$PYTHON" -m deployment create-install-backup \
  --config "$CONFIG" --destination "$BACKUP" --source-root "$RELEASE_SOURCE" \
  --source-manifest "$SOURCE_MANIFEST" --confirm-private-storage
BACKUP_MANIFEST_SHA="$(tr -d '\r\n' < "$BACKUP/COMPLETE")"

"$PYTHON" -m deployment bootstrap-install \
  --directory "$MANAGED" --source-root "$RELEASE_SOURCE" --source-manifest "$SOURCE_MANIFEST" \
  --expected-source "$EXPECTED_SOURCE" --python "$PYTHON" --config "$CONFIG" \
  --schema-sha256 "$EXPECTED_SCHEMA" --confirm-private-storage

"$PYTHON" -m deployment install-readiness \
  --config "$CONFIG" --source-root "$RELEASE_SOURCE" --source-manifest "$SOURCE_MANIFEST" \
  --backup-directory "$BACKUP" --manifest-sha256 "$BACKUP_MANIFEST_SHA" \
  --expected-source "$EXPECTED_SOURCE" --expected-schema "$EXPECTED_SCHEMA" \
  --platform linux --project "$TOOLING" --user "$(id -un)" --python "$PYTHON" \
  --managed-installation "$MANAGED"

"$PYTHON" -m deployment.service_files \
  --platform linux --project "$TOOLING" --user "$(id -un)" --output "$UNIT_STAGE" \
  --instance-config "$CONFIG" --python "$PYTHON" --managed-installation "$MANAGED"
install -m 600 "$UNIT_STAGE"/chief-agent-dashboard.service "$UNIT_DEST"/
install -m 600 "$UNIT_STAGE"/chief-agent-worker.service "$UNIT_DEST"/
install -m 600 "$UNIT_STAGE"/chief-agent-scheduler.service "$UNIT_DEST"/

echo "Installing the HTTPS/static PWA proxy..."
case "$(uname -m)" in x86_64) CADDY_ARCH=amd64;; aarch64|arm64) CADDY_ARCH=arm64;; esac
CADDY_FILE="caddy_${CADDY_VERSION}_linux_${CADDY_ARCH}.tar.gz"
CADDY_TMP="$STATE/caddy-download"
mkdir -p "$CADDY_TMP" "$CADDY_ROOT/bin" "$CADDY_ROOT/data" "$CADDY_ROOT/config"
curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_checksums.txt" -o "$CADDY_TMP/checksums.txt"
echo "$CADDY_CHECKSUMS_SHA256  $CADDY_TMP/checksums.txt" | sha256sum -c -
curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/${CADDY_FILE}" -o "$CADDY_TMP/$CADDY_FILE"
( cd "$CADDY_TMP" && grep "  ${CADDY_FILE}$" checksums.txt | sha256sum -c - )
tar -xzf "$CADDY_TMP/$CADDY_FILE" -C "$CADDY_ROOT/bin" caddy
chmod 700 "$CADDY_ROOT/bin/caddy"
sudo setcap cap_net_bind_service=+ep "$CADDY_ROOT/bin/caddy"

TLS_LINE=""
if [[ "$TLS_MODE" == LOCAL ]]; then TLS_LINE="    tls internal"; fi
cat > "$CADDYFILE" <<EOF
${PUBLIC_HOST} {
${TLS_LINE}
    encode zstd gzip
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "no-referrer"
        Permissions-Policy "camera=(self), microphone=(), geolocation=()"
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:8765 {
            header_up Host {host}
            header_up X-Forwarded-Proto https
        }
    }
    handle_path /owner/* {
        root * ${PWA_ROOT}/owner/dist
        try_files {path} /index.html
        file_server
    }
    handle_path /staff/* {
        root * ${PWA_ROOT}/staff/dist
        try_files {path} /index.html
        file_server
    }
    handle_path /jobs/* {
        root * ${PWA_ROOT}/jobs/dist
        try_files {path} /index.html
        file_server
    }
    respond 404
}
EOF
"$CADDY_ROOT/bin/caddy" validate --config "$CADDYFILE" --adapter caddyfile

cat > "$UNIT_DEST/chief-tls-proxy.service" <<EOF
[Unit]
Description=Chief HTTPS and PWA proxy
After=network-online.target chief-agent-dashboard.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=${CADDY_ROOT}/bin/caddy run --config ${CADDYFILE} --adapter caddyfile
ExecReload=${CADDY_ROOT}/bin/caddy reload --config ${CADDYFILE} --adapter caddyfile
Restart=on-failure
RestartSec=10
NoNewPrivileges=yes
UMask=0077
Environment=XDG_DATA_HOME=${CADDY_ROOT}/data
Environment=XDG_CONFIG_HOME=${CADDY_ROOT}/config

[Install]
WantedBy=default.target
EOF
chmod 600 "$UNIT_DEST/chief-tls-proxy.service"

systemctl --user daemon-reload
systemctl --user enable chief-agent-dashboard.service chief-agent-worker.service chief-agent-scheduler.service chief-tls-proxy.service
"$PYTHON" -c 'import json,sys; from deployment.launch import configure; from deployment.linux_service_control import start_services; print(json.dumps(start_services(configure(sys.argv[1])),sort_keys=True))' "$CONFIG"
systemctl --user restart chief-tls-proxy.service

PORT="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["dashboard_port"])' "$CONFIG")"
curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null
systemctl --user is-active --quiet chief-agent-dashboard.service
systemctl --user is-active --quiet chief-agent-worker.service
systemctl --user is-active --quiet chief-agent-scheduler.service
systemctl --user is-active --quiet chief-tls-proxy.service

if [[ "$TLS_MODE" == LOCAL ]]; then
  CA_ROOT="$CADDY_ROOT/data/caddy/pki/authorities/local/root.crt"
  for _ in {1..20}; do [[ -f "$CA_ROOT" ]] && break; sleep 1; done
  [[ -f "$CA_ROOT" ]] || die "Caddy local CA was not created."
  curl --cacert "$CA_ROOT" --resolve "${PUBLIC_HOST}:443:127.0.0.1" -fsS "https://${PUBLIC_HOST}/api/health" >/dev/null
  DEVICE_SETUP="$HOME/Chief_Device_Setup"
  mkdir -p "$DEVICE_SETUP"; chmod 700 "$DEVICE_SETUP"
  install -m 644 "$CA_ROOT" "$DEVICE_SETUP/Chief-Local-CA.crt"
  cat > "$DEVICE_SETUP/README.txt" <<EOF
Chief device setup for ${CHIEF_OWNER_NAME}

Chief address: https://${PUBLIC_HOST}/owner/
Certificate: Chief-Local-CA.crt

On iPhone/iPad:
1. Transfer and open Chief-Local-CA.crt.
2. Install the downloaded profile in Settings > General > VPN & Device Management.
3. Go to Settings > General > About > Certificate Trust Settings.
4. Enable full trust for the Chief local CA.
5. Open the Chief address above in Safari.

Only the public CA certificate is exported here. No private TLS key is copied.
EOF
  chmod 600 "$DEVICE_SETUP/README.txt"
fi

printf '\nINSTALLATION_COMPLETE\n'
echo "Chief owner: $CHIEF_OWNER_NAME"
echo "Owner PWA: https://${PUBLIC_HOST}/owner/"
echo "Staff PWA: https://${PUBLIC_HOST}/staff/"
echo "Job PWA:   https://${PUBLIC_HOST}/jobs/"
echo "Archive:   $ARCHIVE"
echo "Log:       $LOG"
if [[ "$TLS_MODE" == LOCAL ]]; then
  echo "Local discovery: automatic mDNS (${PUBLIC_HOST})."
  echo "Phone setup certificate: $HOME/Chief_Device_Setup/Chief-Local-CA.crt"
  echo "Phone instructions: $HOME/Chief_Device_Setup/README.txt"
else
  echo "TLS mode: PUBLIC. The administrator-supplied CHIEF_PUBLIC_HOST must already have working DNS/inbound validation."
fi