"""Tests for Chief Agent deployment contract interface and capability wrappers.

Verifies contract metadata, version negotiation, CLI, and executes end-to-end
invocations of every exposed deployment capability wrapper against Chief's real APIs.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from deployment.contract import (
    CHIEF_AGENT_BASELINE_COMMIT,
    DEPLOYMENT_CONTRACT_VERSION,
    bootstrap_installation,
    bootstrap_ownership,
    build_source_manifest,
    create_install_backup,
    create_installation_identity,
    derive_display_name,
    derive_local_host_label,
    derive_local_hostname,
    get_contract_info,
    installation_readiness,
    prepare_production,
    service_definitions,
    stop_linux_services,
    verify_installer_compatibility,
    write_service_definitions,
)
from tests.checkpoint_f_fixture import PASSWORD, e_base, f_fixture


def test_contract_metadata_fields():
    info = get_contract_info()
    assert info.contract_version == DEPLOYMENT_CONTRACT_VERSION
    assert info.chief_agent_commit == CHIEF_AGENT_BASELINE_COMMIT
    for cap in [
        "source_manifest",
        "prepare_instance",
        "bootstrap_install",
        "install_readiness",
        "install_backup",
        "service_files",
        "linux_service_control",
        "ownership_bootstrap",
        "installation_identity",
    ]:
        assert cap in info.supported_capabilities
    assert info.features["credential_redaction"] is True
    assert info.features["isolated_preparation"] is True


def test_contract_compatibility_verification():
    assert verify_installer_compatibility("1.0.0") is True
    assert verify_installer_compatibility("1.2.3") is True
    assert verify_installer_compatibility("2.0.0") is False
    assert verify_installer_compatibility("0.9.0") is False
    assert verify_installer_compatibility("") is False
    assert verify_installer_compatibility("invalid") is False


def test_contract_cli_version():
    cmd = [sys.executable, "-m", "deployment.contract", "--version"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert res.stdout.strip() == DEPLOYMENT_CONTRACT_VERSION


def test_contract_cli_info():
    cmd = [sys.executable, "-m", "deployment.contract", "--info"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    assert data["contract_version"] == DEPLOYMENT_CONTRACT_VERSION
    assert data["chief_agent_commit"] == CHIEF_AGENT_BASELINE_COMMIT
    assert "prepare_instance" in data["supported_capabilities"]
    assert "install_readiness" in data["supported_capabilities"]


def test_contract_cli_verify():
    cmd = [sys.executable, "-m", "deployment.contract", "--verify", "1.0.0"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    assert data["compatible"] is True

    cmd_fail = [sys.executable, "-m", "deployment.contract", "--verify", "2.0.0"]
    res_fail = subprocess.run(cmd_fail, capture_output=True, text=True)
    assert res_fail.returncode == 1
    data_fail = json.loads(res_fail.stdout)
    assert data_fail["compatible"] is False


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Chief Test"], check=True, capture_output=True)
    (repo / "module.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "module.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, commit


def test_contract_capability_source_manifest(tmp_path):
    repo, commit = _git_repo(tmp_path)
    output = tmp_path / "manifest.json"

    result = build_source_manifest(repo, output, expected_git_commit=commit)
    assert result["status"] == "SOURCE_MANIFEST_BUILT"
    assert result["git_commit"] == commit
    assert result["file_count"] == 1
    assert output.exists()
    content = json.loads(output.read_text(encoding="utf-8"))
    assert "module.py" in content["files"]


def test_contract_capability_service_definitions(tmp_path):
    # Test Linux service definitions dictionary
    linux_defs = service_definitions(
        platform="linux",
        project="/home/chief/app",
        user="chief",
        instance_config="/home/chief/app/config.json",
    )
    assert "chief-agent-dashboard.service" in linux_defs
    assert "chief-agent-worker.service" in linux_defs
    assert "chief-agent-scheduler.service" in linux_defs
    assert b"ConditionUser=chief" in linux_defs["chief-agent-dashboard.service"]

    # Test writing service definition files to target directory
    out_dir = tmp_path / "services"
    written = write_service_definitions(
        out_dir,
        platform="linux",
        project="/home/chief/app",
        user="chief",
        instance_config="/home/chief/app/config.json",
    )
    assert len(written) == 3
    for p in written:
        assert p.exists()
        assert p.stat().st_size > 0

    # Test Windows task definitions
    win_defs = service_definitions(
        platform="windows",
        project=r"C:\Chief\app",
        user="ChiefUser",
        instance_config=r"C:\Chief\app\config.json",
    )
    assert "chief-agent-dashboard.xml" in win_defs
    assert win_defs["chief-agent-dashboard.xml"].startswith(b"\xff\xfe") or win_defs["chief-agent-dashboard.xml"].startswith(b"\xfe\xff")


def test_contract_capability_prepare_production(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_manifest = tmp_path / "SOURCE_MANIFEST.json"
    source_manifest.write_text("{}", encoding="utf-8")

    # Synthetic browser runtime
    exe = tmp_path / "browser"
    exe.write_bytes(b"browser-binary")
    import hashlib
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    browser_cfg = tmp_path / "browser.json"
    browser_cfg.write_text(json.dumps({"executable": str(exe), "sha256": digest, "version": "140.0.0.1"}), encoding="utf-8")

    def fake_isolated(root, source_root_arg, source_manifest_arg, username, password, *, organization_name=None, farm_name=None):
        root = Path(root)
        root.mkdir(mode=0o700)
        db = root / "preview.sqlite3"
        with closing(sqlite3.connect(db)) as con:
            con.execute("CREATE TABLE test_table(id INTEGER PRIMARY KEY)")
            con.commit()
        return {
            "status": "PREVIEW_PREPARED",
            "database": str(db),
            "schema_sha256": "a" * 64,
            "owner_id": "owner-123",
            "organization_id": "org-123",
            "organization_name": organization_name,
            "farm_id": "farm-123",
            "farm_name": farm_name,
        }

    from deployment import prepare_instance
    monkeypatch.setattr(prepare_instance, "prepare_isolated", fake_isolated)
    monkeypatch.setattr("deployment.schema.preflight", lambda db, schema: None)

    root = tmp_path / "prod_state"
    config = tmp_path / "prod_config.json"
    result = prepare_production(
        root=root,
        config=config,
        source_root=source_root,
        source_manifest=source_manifest,
        username="owner",
        password="secret-password",
        public_host="chief.example.local",
        browser_runtime=browser_cfg,
        farm_operations="ENABLED",
        organization_name="Contract Org",
        farm_name="Contract Farm",
    )

    assert result["status"] == "PRODUCTION_PREPARED"
    assert result["organization_id"] == "org-123"
    assert result["farm_id"] == "farm-123"
    assert config.exists()
    cfg_data = json.loads(config.read_text(encoding="utf-8"))
    assert cfg_data["mode"] == "PRODUCTION"
    assert cfg_data["farm_operations"] == "ENABLED"


def test_contract_capability_create_install_backup(tmp_path, monkeypatch):
    database = tmp_path / "state.sqlite3"
    database.write_bytes(b"synthetic-db")
    config = tmp_path / "instance.json"
    config.write_text("{}", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    destination = tmp_path / "backup_dest"

    import deployment.launch
    import operations.backup
    schema = "b" * 64
    source_sha = "a" * 64
    monkeypatch.setattr(deployment.launch, "configure", lambda filename: SimpleNamespace(
        mode="production", database=database, schema_sha256=schema))
    monkeypatch.setattr(operations.backup, "create_backup", lambda *a, **kw: {
        "backup_id": "b-1", "manifest_sha256": "c" * 64, "source_tree_sha256": source_sha,
        "schema_sha256": schema, "coverage": "database-only"
    })

    result = create_install_backup(
        config=config,
        destination=destination,
        source_root=source,
        source_manifest=manifest,
        private_storage_confirmed=True,
    )
    assert result["status"] == "INSTALLATION_BACKUP_CREATED"
    assert result["source_tree_sha256"] == source_sha
    assert result["schema_sha256"] == schema


def test_contract_capability_installation_readiness(tmp_path, monkeypatch):
    config = (tmp_path / "instance.json").resolve()
    config.write_text("{}", encoding="utf-8")
    source = (tmp_path / "source").resolve()
    source.mkdir()
    manifest = (tmp_path / "source_manifest.json").resolve()
    manifest.write_text("{}", encoding="utf-8")
    backup = (tmp_path / "backup").resolve()
    backup.mkdir()

    schema = "a" * 64
    source_sha = "b" * 64
    manifest_sha = "c" * 64
    instance = SimpleNamespace(mode="production", state_root=tmp_path, schema_sha256=schema)

    monkeypatch.setattr("deployment.launch.configure", lambda p: instance)
    monkeypatch.setattr("operations.storage_health.storage_readiness", lambda r: {"scope": "STARTUP_PROBE_ONLY"})
    monkeypatch.setattr("browser.runtime.launch_options", lambda: {"executable_path": "/synthetic/browser", "chromium_sandbox": True})
    monkeypatch.setattr("operations.backup.source_identity", lambda r, m: source_sha)
    monkeypatch.setattr("deployment.runtime_profiles.plans_from_source", lambda sr, r: [
        {"candidate_id": "runtime-exec", "status": "READY"},
    ])
    monkeypatch.setattr("operations.backup.verify_backup", lambda *a, **kw: {
        "backup_id": "backup-001", "coverage": "database-only"
    })

    is_win = sys.platform == "win32"
    plat = "windows" if is_win else "linux"
    proj = r"C:\Chief\app" if is_win else "/home/chief/app"
    usr = "chief"
    python_path = Path(r"C:\Chief\python.exe") if is_win else Path("/home/chief/python")

    result = installation_readiness(
        config=config,
        source_root=source,
        source_manifest=manifest,
        backup_directory=backup,
        manifest_sha256=manifest_sha,
        expected_source=source_sha,
        expected_schema=schema,
        platform=plat,
        project=proj,
        user=usr,
        python=python_path,
    )

    assert result["status"] == "SOURCE_READY_FOR_INSTALL_REVIEW"
    assert result["source_tree_sha256"] == source_sha
    assert result["schema_sha256"] == schema
    expected_unit = "chief-agent-dashboard.xml" if is_win else "chief-agent-dashboard.service"
    assert expected_unit in result["service_definitions"]


def test_contract_capability_bootstrap_installation(tmp_path, monkeypatch):
    managed_dir = tmp_path / "managed"
    source = tmp_path / "source"
    source.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")

    source_sha = "a" * 64
    schema_sha = "b" * 64

    import operations.backup
    from update_center.managed import ManagedInstallation
    monkeypatch.setattr(operations.backup, "source_identity", lambda r, m: source_sha)

    mock_inst = SimpleNamespace(
        root=managed_dir,
        source=lambda sha: (managed_dir / "src", managed_dir / "manifest"),
        selection=lambda: source_sha,
    )
    monkeypatch.setattr(ManagedInstallation, "provision", lambda *a, **kw: mock_inst)

    result = bootstrap_installation(
        directory=managed_dir,
        source_root=source,
        source_manifest=manifest,
        expected_source=source_sha,
        python=Path(sys.executable),
        instance_config=config,
        schema_sha256=schema_sha,
        private_storage_confirmed=True,
    )

    assert result["status"] == "MANAGED_INSTALLATION_PROVISIONED"
    assert result["managed_root"] == str(managed_dir)
    assert result["source_tree_sha256"] == source_sha


def test_contract_capability_ownership_bootstrap(f_fixture):
    result = bootstrap_ownership(
        store=f_fixture.store,
        owner_id=f_fixture.owner.id,
        organization_name="Contract Harvest Corp",
        farm_name="East Field",
    )
    assert result["organization_name"] == "Contract Harvest Corp"
    assert result["farm_name"] == "East Field"
    assert result["organization_id"].startswith("org-")
    assert result["farm_id"].startswith("farm-")


def test_contract_capability_linux_service_control(monkeypatch):
    # Verify non-Linux platform raises expected RuntimeError
    if not sys.platform.startswith("linux"):
        with pytest.raises(RuntimeError, match="Linux user services"):
            stop_linux_services()

    # Monkeypatch to verify Linux invocation calls systemctl
    calls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda cmd: "/bin/systemctl")
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: (calls.append(cmd) or SimpleNamespace(returncode=0)))

    result = stop_linux_services()
    assert result["status"] == "SERVICES_STOPPED"
    assert len(calls) == 1
    assert calls[0][:3] == ["/bin/systemctl", "--user", "stop"]


def test_contract_capability_installation_identity():
    assert derive_display_name("  Contract Farm   ") == "Contract Farm"
    assert derive_local_host_label("Contract Farm") == "chief-contract-farm"
    assert derive_local_hostname("Contract Farm") == "chief-contract-farm.local"

    identity = create_installation_identity("Contract Farm")
    assert identity["owner_display_name"] == "Contract Farm"
    assert identity["network"]["hostname"] == "chief-contract-farm.local"
    assert identity["network"]["mode"] == "LOCAL_MDNS"
