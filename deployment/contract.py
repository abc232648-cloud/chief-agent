"""Chief Agent Deployment Interface & Version Contract.

Exposes stable, versioned deployment interfaces for external installers
(chief-installer) and host-level maintenance workflows without requiring
external callers to understand Chief Agent's internal module topology.
"""
from dataclasses import asdict, dataclass
import json
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
]

CHIEF_AGENT_BASELINE_COMMIT = "819b7d76c9b17e0c58b50c32ea485991f163aeba"


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
    return DeploymentContractInfo(
        contract_version=DEPLOYMENT_CONTRACT_VERSION,
        chief_agent_commit=CHIEF_AGENT_BASELINE_COMMIT,
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


def bootstrap_managed_install(
    directory: Path,
    *,
    source_root: Path,
    source_manifest: Path,
    expected_source: str,
    python: Path,
    instance_config: Path,
    schema_sha256: str,
    private_storage_confirmed: bool = True,
) -> Dict[str, Any]:
    """Provision immutable managed installation source slot."""
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


def verify_install_readiness(
    instance_config: Path,
    *,
    expected_source: str,
    managed_dir: Path,
) -> Dict[str, Any]:
    """Verify that an installation is ready to run before services start."""
    from deployment.install_readiness import verify_readiness as _readiness
    return _readiness(
        instance_config=instance_config,
        expected_source=expected_source,
        managed_dir=managed_dir,
    )


def create_install_backup(
    database: Path,
    destination: Path,
    *,
    source_root: Path,
    source_manifest: Path,
) -> Dict[str, Any]:
    """Create a verified base install backup before service activation."""
    from deployment.install_backup import create_install_backup as _backup
    return _backup(
        database=database,
        destination=destination,
        source_root=source_root,
        source_manifest=source_manifest,
    )


def generate_service_units(
    target_dir: Path,
    *,
    python: Path,
    repo_root: Path,
    config_path: Path,
    caddy_path: Optional[Path] = None,
    caddyfile_path: Optional[Path] = None,
) -> List[Path]:
    """Generate systemd user service unit files."""
    from deployment.service_files import write_service_files as _write_services
    return _write_services(
        target_dir=target_dir,
        python=python,
        repo_root=repo_root,
        config_path=config_path,
        caddy_path=caddy_path,
        caddyfile_path=caddyfile_path,
    )


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
