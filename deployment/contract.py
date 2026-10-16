"""Chief Agent Deployment Interface & Version Contract.

Exposes stable, versioned deployment interfaces for external installers
(chief-installer) and host-level maintenance workflows without requiring
external callers to understand Chief Agent's internal module topology.
"""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

DEPLOYMENT_CONTRACT_VERSION = "1.0.0"
MIN_COMPATIBLE_INSTALLER_VERSION = "1.0.0"
MAX_COMPATIBLE_INSTALLER_VERSION = "1.999.999"

SUPPORTED_CAPABILITIES: List[str] = [
    "source_manifest",
    "prepare_instance",
    "bootstrap_install",
    "install_readiness",
    "install_backup",
    "service_files",
    "linux_service_control",
    "ownership_bootstrap",
    "installation_identity",
]

CHIEF_AGENT_BASELINE_COMMIT = "a9a1f33e42f1cfc7c73811ffd13ae3db0963cb47"


def _detect_current_commit(root: Path) -> str:
    try:
        import subprocess
        res = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        c = res.stdout.strip().lower()
        if len(c) == 40 and all(ch in "0123456789abcdef" for ch in c):
            return c
    except Exception:
        pass
    return CHIEF_AGENT_BASELINE_COMMIT


@dataclass(frozen=True)
class DeploymentContractInfo:
    contract_version: str
    chief_agent_commit: str
    supported_capabilities: List[str]
    min_compatible_installer_version: str
    max_compatible_installer_version: str
    features: Dict[str, bool]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_contract_info() -> DeploymentContractInfo:
    """Return structured contract metadata and capability profile."""
    current_commit = _detect_current_commit(Path(__file__).resolve().parents[1])
    return DeploymentContractInfo(
        contract_version=DEPLOYMENT_CONTRACT_VERSION,
        chief_agent_commit=current_commit,
        supported_capabilities=list(SUPPORTED_CAPABILITIES),
        min_compatible_installer_version=MIN_COMPATIBLE_INSTALLER_VERSION,
        max_compatible_installer_version=MAX_COMPATIBLE_INSTALLER_VERSION,
        features={
            "zero_loss_archive": True,
            "mdns_discovery": True,
            "tls_proxy": True,
            "systemd_user_units": True,
            "credential_redaction": True,
            "isolated_preparation": True,
        },
    )


def verify_installer_compatibility(installer_contract_version: str) -> bool:
    """Check if an external installer contract version is compatible with this agent."""
    if not installer_contract_version:
        return False
    parts = installer_contract_version.strip().split(".")
    try:
        major = int(parts[0])
        # Major version 1 is compatible with 1.x.x
        return major == 1
    except (ValueError, IndexError):
        return False


# --- Capability 1: Source Manifest ---

def build_source_manifest(
    source_root: Path,
    output: Path,
    *,
    expected_git_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Build deterministic source manifest from a clean, pinned Git checkout."""
    from deployment.source_manifest import build_source_manifest as _build
    return _build(source_root=source_root, output=output, expected_git_commit=expected_git_commit)


# --- Capability 2: Prepare Production Instance ---

def prepare_production(
    root: Path,
    config: Path,
    source_root: Path,
    source_manifest: Path,
    username: str,
    password: str,
    *,
    public_host: str,
    browser_runtime: Path,
    dashboard_port: int = 8765,
    trusted_tls_proxy: str = "127.0.0.1",
    farm_operations: str = "DISABLED",
    organization_name: Optional[str] = None,
    farm_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute isolated production preparation and promotion."""
    from deployment.prepare_instance import prepare_production as _prepare
    return _prepare(
        root=root,
        config=config,
        source_root=source_root,
        source_manifest=source_manifest,
        username=username,
        password=password,
        public_host=public_host,
        browser_runtime=browser_runtime,
        dashboard_port=dashboard_port,
        trusted_tls_proxy=trusted_tls_proxy,
        farm_operations=farm_operations,
        organization_name=organization_name,
        farm_name=farm_name,
    )


# --- Capability 3: Bootstrap Managed Installation ---

def bootstrap_installation(
    directory: Path,
    *,
    source_root: Path,
    source_manifest: Path,
    expected_source: str,
    python: Path,
    instance_config: Path,
    schema_sha256: str,
    private_storage_confirmed: bool = False,
) -> Dict[str, Any]:
    """Provision one immutable managed installation source slot without starting services."""
    from deployment.bootstrap_install import bootstrap_installation as _bootstrap
    return _bootstrap(
        directory=directory,
        source_root=source_root,
        source_manifest=source_manifest,
        expected_source=expected_source,
        python=python,
        instance_config=instance_config,
        schema_sha256=schema_sha256,
        private_storage_confirmed=private_storage_confirmed,
    )

# Backward-compatible alias
bootstrap_managed_install = bootstrap_installation


# --- Capability 4: Installation Readiness ---

def installation_readiness(
    config: Path,
    *,
    source_root: Path,
    source_manifest: Path,
    backup_directory: Path,
    manifest_sha256: str,
    expected_source: str,
    expected_schema: str,
    platform: str,
    project: str,
    user: str,
    python: Optional[Any] = None,
    managed_installation: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return metadata-only readiness evidence; no deployment action is performed."""
    from deployment.install_readiness import installation_readiness as _readiness
    return _readiness(
        config=config,
        source_root=source_root,
        source_manifest=source_manifest,
        backup_directory=backup_directory,
        manifest_sha256=manifest_sha256,
        expected_source=expected_source,
        expected_schema=expected_schema,
        platform=platform,
        project=project,
        user=user,
        python=str(python) if python is not None else None,
        managed_installation=str(managed_installation) if managed_installation is not None else None,
    )

# Backward-compatible alias
verify_install_readiness = installation_readiness


# --- Capability 5: Install Backup ---

def create_install_backup(
    config: Path,
    destination: Path,
    *,
    source_root: Path,
    source_manifest: Path,
    private_storage_confirmed: bool = False,
) -> Dict[str, Any]:
    """Create a database-only installation backup pinned to an exact source manifest."""
    from deployment.install_backup import create_install_backup as _backup
    return _backup(
        config=config,
        destination=destination,
        source_root=source_root,
        source_manifest=source_manifest,
        private_storage_confirmed=private_storage_confirmed,
    )


# --- Capability 6: Service Files & Definitions ---

def service_definitions(
    platform: str,
    project: str,
    user: str,
    *,
    instance_config: str,
    python: Optional[str] = None,
    managed_installation: Optional[str] = None,
) -> Dict[str, bytes]:
    """Generate reviewable startup definitions dictionary mapping filename to definition bytes."""
    from deployment.service_files import definitions as _definitions
    return _definitions(
        platform=platform,
        project=project,
        user=user,
        instance_config=instance_config,
        python=python,
        managed_installation=managed_installation,
    )

definitions = service_definitions


def write_service_definitions(
    target_dir: Path,
    platform: str,
    project: str,
    user: str,
    *,
    instance_config: str,
    python: Optional[str] = None,
    managed_installation: Optional[str] = None,
) -> List[Path]:
    """Generate and write service definition files into target_dir."""
    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)
    files = service_definitions(
        platform=platform,
        project=project,
        user=user,
        instance_config=instance_config,
        python=python,
        managed_installation=managed_installation,
    )
    written: List[Path] = []
    for name, data in files.items():
        dest = target_path / name
        dest.write_bytes(data)
        written.append(dest)
    return written

generate_service_units = write_service_definitions


# --- Capability 7: Linux Service Control ---

def stop_linux_services(*, timeout: int = 60) -> Dict[str, Any]:
    """Stop writers before source selection using systemd --user."""
    from deployment.linux_service_control import stop_services as _stop
    return _stop(timeout=timeout)


def start_linux_services(instance: Any, *, timeout: int = 90) -> Dict[str, Any]:
    """Start the selected source and require fresh READY records for all components."""
    from deployment.linux_service_control import start_services as _start
    return _start(instance, timeout=timeout)


# --- Capability 8: Ownership Bootstrap ---

def bootstrap_ownership(
    store: Any,
    owner_id: str,
    organization_name: str,
    farm_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Seed the first organization/farm into an otherwise empty ownership ledger."""
    from deployment.ownership_bootstrap import bootstrap_initial as _bootstrap
    return _bootstrap(
        store=store,
        owner_id=owner_id,
        organization_name=organization_name,
        farm_name=farm_name,
    )

bootstrap_initial = bootstrap_ownership


# --- Capability 9: Installation Identity ---

def derive_display_name(raw: str) -> str:
    """Normalize owner display name."""
    from deployment.installation_identity import display_name as _display_name
    return _display_name(raw)


def derive_local_host_label(owner_name: str) -> str:
    """Derive RFC 1035 safe host label from owner display name."""
    from deployment.installation_identity import local_host_label as _label
    return _label(owner_name)


def derive_local_hostname(owner_name: str) -> str:
    """Derive local mDNS hostname from owner display name."""
    from deployment.installation_identity import local_hostname as _hostname
    return _hostname(owner_name)


def create_installation_identity(owner_display_name: str, *, hostname: Optional[str] = None) -> Dict[str, Any]:
    """Create initial installation identity dictionary."""
    from deployment.installation_identity import new_identity as _new_id
    return _new_id(owner_display_name, hostname=hostname)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI handler for inspecting and querying deployment contract."""
    import argparse
    parser = argparse.ArgumentParser(description="Chief Agent Deployment Interface Contract")
    parser.add_argument("--version", action="store_true", help="Print contract version")
    parser.add_argument("--info", action="store_true", help="Print JSON contract metadata")
    parser.add_argument("--verify", type=str, help="Verify compatibility with an installer contract version")

    args = parser.parse_args(argv)
    if args.version:
        print(DEPLOYMENT_CONTRACT_VERSION)
        return 0
    if args.info:
        print(json.dumps(get_contract_info().to_dict(), indent=2))
        return 0
    if args.verify:
        compatible = verify_installer_compatibility(args.verify)
        print(json.dumps({"installer_version": args.verify, "compatible": compatible}))
        return 0 if compatible else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
