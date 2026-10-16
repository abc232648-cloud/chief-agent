"""Tests for Chief Agent deployment contract interface and version compatibility."""
import json
import subprocess
import sys

from deployment.contract import (
    CHIEF_AGENT_BASELINE_COMMIT,
    DEPLOYMENT_CONTRACT_VERSION,
    get_contract_info,
    verify_installer_compatibility,
)


def test_contract_metadata_fields():
    info = get_contract_info()
    assert info.contract_version == DEPLOYMENT_CONTRACT_VERSION
    assert info.chief_agent_commit == CHIEF_AGENT_BASELINE_COMMIT
    assert "prepare_instance" in info.supported_capabilities
    assert "source_manifest" in info.supported_capabilities
    assert "bootstrap_install" in info.supported_capabilities
    assert "install_readiness" in info.supported_capabilities
    assert "install_backup" in info.supported_capabilities
    assert "service_files" in info.supported_capabilities
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
