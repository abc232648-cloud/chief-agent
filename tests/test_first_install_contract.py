from pathlib import Path
import shutil
import subprocess


def test_first_install_launcher_and_implementation_are_wired_for_clean_host():
    root=Path(__file__).resolve().parents[1]
    launcher_path=root/'deployment/install_chief.sh'
    implementation_path=root/'deployment/install_chief_xubuntu.sh'
    launcher=launcher_path.read_text(encoding='utf-8')
    implementation=implementation_path.read_text(encoding='utf-8')

    assert 'PLAYWRIGHT_BROWSERS_PATH="$HOME/.local/share/chief/browsers"' in launcher
    assert 'PYTHONPATH="$HOME/.local/share/chief/tooling' in launcher
    assert 'sudo loginctl enable-linger "$(id -un)"' in launcher
    assert 'export PATH="$NODE_ROOT/bin:$PATH"' in launcher
    assert "text.replace(anchor,'UMask=0077\\n',1)" in launcher

    # A fresh install archives rather than reuses the former checkout/runtime/state.
    assert 'move_path "$HOME/chief-agent" home' not in implementation  # paths are grouped in one loop
    assert '"$HOME/chief-agent" "$HOME/chief-runtime"' in implementation
    assert 'This intentionally archives production too' in implementation
    assert 'sudo -v' in implementation
    assert 'python3 -m venv "$RUNTIME"' in implementation
    assert 'pip install -r "$RELEASE_SOURCE/requirements.txt"' in implementation
    assert 'playwright install --with-deps chromium' in implementation
    assert 'NODE_VERSION="22.23.3"' in implementation
    assert 'build_pwa owner' in implementation and 'build_pwa staff' in implementation and 'build_pwa jobs' in implementation
    assert 'prepare-production' in implementation
    assert 'bootstrap-install' in implementation
    assert 'install-readiness' in implementation
    assert 'chief-tls-proxy.service' in implementation
    assert 'INSTALLATION_COMPLETE' in implementation

    bash=shutil.which('bash')
    if bash:
        subprocess.run([bash,'-n',str(launcher_path)],check=True)
        subprocess.run([bash,'-n',str(implementation_path)],check=True)


def test_first_install_does_not_reuse_old_runtime_as_bootstrap_prerequisite():
    root=Path(__file__).resolve().parents[1]
    implementation=(root/'deployment/install_chief_xubuntu.sh').read_text(encoding='utf-8')
    assert '/home/ape-xubuntu/chief-runtime' not in implementation
    assert '/home/ape-xubuntu/chief-agent' not in implementation
    assert 'PYTHON="$RUNTIME/bin/python"' in implementation
    assert 'git clone --no-local "$REPO_URL" "$TOOLING"' in implementation
