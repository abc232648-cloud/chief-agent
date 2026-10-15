# Deploy Chief now — Folio/Xubuntu

This is the canonical first-deployment path for the install-closure release. It deliberately does **not** require a hand-built ZIP.

## Packaging rule

- **First/bootstrap deployment:** clean pinned Git checkout + generated `SOURCE_MANIFEST.json`.
- **Later managed updates:** signed Chief update ZIPs validated by the existing update-center archive/trust path.
- **Audit/handoff ZIP:** optional convenience artifact; it is not a runtime prerequisite.

Do not manually assemble arbitrary ZIP files for bootstrap installation.

## Why the historical `30d2...` pin is not reused

`release/release-set-2026-10-14.json` is historical release evidence. Its backend `tested_source_tree_sha256` belongs to the source snapshot qualified by that contract. The published tag points at a later commit. Deployment therefore builds a deterministic manifest for the **exact tagged checkout being installed** and creates a fresh database-only installation backup pinned to that resulting source identity. Historical evidence remains unchanged.

## Preconditions

The Folio must already have:

1. the stable tooling checkout at `/home/ape-xubuntu/chief-agent`;
2. the qualified Python runtime at `/home/ape-xubuntu/chief-runtime/chief-python`;
3. the prepared production instance configuration and database created by the earlier preparation flow;
4. Git and systemd user services available.

The only value that cannot be safely guessed is the absolute production instance-config path. Set `CONFIG` below to that existing file.

## Canonical deployment sequence

Run from a normal shell as the `ape-xubuntu` account. Do not use `sudo` for Chief user services.

```bash
set -euo pipefail

TOOLING=/home/ape-xubuntu/chief-agent
PYTHON=/home/ape-xubuntu/chief-runtime/chief-python
CONFIG=/ABSOLUTE/PATH/TO/YOUR/EXISTING/production-instance.json
RELEASE_REF=chief-install-closure-2026-10-14
RELEASE_COMMIT=0d96fdf979b9eba0f3deb0024278f5c2557a5319
RELEASE_SOURCE=/home/ape-xubuntu/chief-release-2026-10-14
META=/home/ape-xubuntu/.local/state/chief-release-metadata/2026-10-14
SOURCE_MANIFEST="$META/SOURCE_MANIFEST.json"
BACKUP=/home/ape-xubuntu/.local/state/chief-install-backups/chief-install-closure-2026-10-14-final-tag
MANAGED=/home/ape-xubuntu/.local/share/chief-managed
UNIT_STAGE=/home/ape-xubuntu/.local/state/chief-service-units/install-closure-2026-10-14
UNIT_DEST=/home/ape-xubuntu/.config/systemd/user

cd "$TOOLING"
git fetch --tags origin
git switch main
git pull --ff-only origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
test -x "$PYTHON"
test -f "$CONFIG"

# Keep the production source immutable and detached from the tooling checkout.
if [ ! -d "$RELEASE_SOURCE/.git" ] && [ ! -f "$RELEASE_SOURCE/.git" ]; then
  test ! -e "$RELEASE_SOURCE"
  git worktree add --detach "$RELEASE_SOURCE" "$RELEASE_REF"
fi
test "$(git -C "$RELEASE_SOURCE" rev-parse HEAD)" = "$RELEASE_COMMIT"
test -z "$(git -C "$RELEASE_SOURCE" status --porcelain --untracked-files=no)"

install -d -m 700 "$META" "$(dirname "$BACKUP")" "$(dirname "$MANAGED")" "$(dirname "$UNIT_STAGE")"
rm -f "$SOURCE_MANIFEST"

# 1. Deterministic manifest for the exact final-tag bytes.
"$PYTHON" -m deployment build-source-manifest \
  --source-root "$RELEASE_SOURCE" \
  --output "$SOURCE_MANIFEST" \
  --expected-git-commit "$RELEASE_COMMIT"

EXPECTED_SOURCE="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source_tree_sha256"])' "$SOURCE_MANIFEST")"
EXPECTED_SCHEMA="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["schema_sha256"])' "$CONFIG")"

# 2. Fresh backup pinned to those exact source bytes. The destination must be new.
test ! -e "$BACKUP"
"$PYTHON" -m deployment create-install-backup \
  --config "$CONFIG" \
  --destination "$BACKUP" \
  --source-root "$RELEASE_SOURCE" \
  --source-manifest "$SOURCE_MANIFEST" \
  --confirm-private-storage

BACKUP_MANIFEST_SHA="$(tr -d '\r\n' < "$BACKUP/COMPLETE")"

# 3. Provision the immutable managed source slot. Bootstrap never starts services.
test ! -e "$MANAGED"
"$PYTHON" -m deployment bootstrap-install \
  --directory "$MANAGED" \
  --source-root "$RELEASE_SOURCE" \
  --source-manifest "$SOURCE_MANIFEST" \
  --expected-source "$EXPECTED_SOURCE" \
  --python "$PYTHON" \
  --config "$CONFIG" \
  --schema-sha256 "$EXPECTED_SCHEMA" \
  --confirm-private-storage

# 4. Run readiness against the same managed-launch service shape we will install.
"$PYTHON" -m deployment install-readiness \
  --config "$CONFIG" \
  --source-root "$RELEASE_SOURCE" \
  --source-manifest "$SOURCE_MANIFEST" \
  --backup-directory "$BACKUP" \
  --manifest-sha256 "$BACKUP_MANIFEST_SHA" \
  --expected-source "$EXPECTED_SOURCE" \
  --expected-schema "$EXPECTED_SCHEMA" \
  --platform linux \
  --project "$TOOLING" \
  --user ape-xubuntu \
  --python "$PYTHON" \
  --managed-installation "$MANAGED"

# 5. Generate exactly the managed systemd-user units reviewed by readiness.
test ! -e "$UNIT_STAGE"
"$PYTHON" -m deployment.service_files \
  --platform linux \
  --project "$TOOLING" \
  --user ape-xubuntu \
  --output "$UNIT_STAGE" \
  --instance-config "$CONFIG" \
  --python "$PYTHON" \
  --managed-installation "$MANAGED"

install -d -m 700 "$UNIT_DEST"
install -m 600 "$UNIT_STAGE"/chief-agent-dashboard.service "$UNIT_DEST"/
install -m 600 "$UNIT_STAGE"/chief-agent-worker.service "$UNIT_DEST"/
install -m 600 "$UNIT_STAGE"/chief-agent-scheduler.service "$UNIT_DEST"/
systemctl --user daemon-reload
systemctl --user enable chief-agent-dashboard.service chief-agent-worker.service chief-agent-scheduler.service

# 6. Start through Chief's existing lifecycle verifier and require fresh READY records.
"$PYTHON" -c 'import json,sys; from deployment.launch import configure; from deployment.linux_service_control import start_services; print(json.dumps(start_services(configure(sys.argv[1])),sort_keys=True))' "$CONFIG"

# 7. Final local status evidence.
systemctl --user --no-pager --full status chief-agent-dashboard.service chief-agent-worker.service chief-agent-scheduler.service
PORT="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("dashboard_port",8765))' "$CONFIG")"
curl -fsS "http://127.0.0.1:${PORT}/api/health"
```

Expected high-level results are:

- `SOURCE_MANIFEST_BUILT`
- `INSTALLATION_BACKUP_CREATED`
- `MANAGED_INSTALLATION_PROVISIONED`
- `SOURCE_READY_FOR_INSTALL_REVIEW`
- `SERVICES_READY`
- `/api/health` returns the application's healthy/ready response.

## After backend deployment

The reverse proxy/TLS route and physical Android/iPhone PWA acceptance remain live-environment checks. They should be performed against this running deployment and recorded as acceptance evidence; they do not require rebuilding the source release.

## Rerun safety

This sequence intentionally refuses to overwrite the managed root, installation backup, release worktree or staged units. If a command stops because one already exists, inspect that existing deployment state rather than deleting it blindly. Managed updates after bootstrap must use the signed update path, not repeat bootstrap over an existing managed root.
