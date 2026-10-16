"""Tests for Chief Agent installation compatibility entrypoint and contract boundary."""
from pathlib import Path
import os
import shutil
import subprocess


def test_first_install_launcher_and_implementation_are_delegated_cleanly():
    root = Path(__file__).resolve().parents[1]
    launcher_path = root / "deployment/install_chief.sh"
    implementation_path = root / "deployment/install_chief_xubuntu.sh"
    launcher = launcher_path.read_text(encoding="utf-8")
    implementation = implementation_path.read_text(encoding="utf-8")

    # Brittle runtime source patching is strictly eliminated
    assert "text.replace" not in launcher
    assert "text.count" not in launcher
    assert "UMask=0077" not in launcher

    # Delegates to the unified chief-installer repository
    assert "chief-installer" in launcher
    assert "exec $HERE/install_chief.sh" in implementation.replace('"', '').replace('"', '')

    # Valid bash syntax
    bash = shutil.which("bash")
    if bash:
        subprocess.run([bash, "-n", str(launcher_path)], check=True)
        subprocess.run([bash, "-n", str(implementation_path)], check=True)


def test_chief_agent_provides_stable_deployment_contract():
    root = Path(__file__).resolve().parents[1]
    contract_path = root / "deployment/contract.py"
    assert contract_path.exists(), "Chief Agent must expose deployment/contract.py"

    from deployment.contract import (
        CHIEF_AGENT_BASELINE_COMMIT,
        DEPLOYMENT_CONTRACT_VERSION,
        get_contract_info,
    )
    info = get_contract_info()
    assert info.contract_version == "1.0.0"
    assert len(info.chief_agent_commit) == 40 and all(c in "0123456789abcdef" for c in info.chief_agent_commit)
    assert "prepare_instance" in info.supported_capabilities
    assert "bootstrap_install" in info.supported_capabilities
    assert "install_readiness" in info.supported_capabilities
