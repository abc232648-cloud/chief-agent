from pathlib import Path
import os
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

    # Normal users provide identity, not DNS/TLS infrastructure details.
    assert 'Who is this Chief for? (your name or organisation name): ' in implementation
    assert 'Hostname devices will use for Chief' not in implementation
    assert 'Use a publicly trusted certificate for this hostname?' not in implementation
    assert 'CHIEF_PUBLIC_HOST' in implementation  # explicit administrator-only public-DNS override
    assert 'deployment.installation_identity' in implementation
    assert 'installation-identity.json' in implementation

    # Farm-enabled installation asks business/farm identity and forwards it to
    # prepare-production instead of treating a hostname as the farm identity.
    assert 'Farm-owning organisation/company [${CHIEF_OWNER_NAME}]' in launcher
    assert 'First farm name: ' in launcher
    assert '--organization-name "$FARM_ORGANIZATION_NAME"' in launcher
    assert '--farm-name "$FIRST_FARM_NAME"' in launcher
    assert '"${OWNERSHIP_ARGS[@]}"' in launcher

    # Local-network naming/discovery and first-device trust material are installer-owned.
    assert 'avahi-daemon avahi-utils libnss-mdns iproute2' in implementation
    assert '/etc/avahi/avahi-daemon.conf' in implementation
    assert 'Chief local hostname was not advertised by this machine.' in implementation
    assert 'Chief_Device_Setup' in implementation
    assert 'Chief-Local-CA.crt' in implementation
    assert 'Certificate Trust Settings' in implementation
    assert '--cacert "$CA_ROOT" --resolve "${PUBLIC_HOST}:443:127.0.0.1"' in implementation

    bash=shutil.which('bash')
    if bash:
        subprocess.run([bash,'-n',str(launcher_path)],check=True)
        subprocess.run([bash,'-n',str(implementation_path)],check=True)
        # The launcher targets Xubuntu. Execute the generated installer only on POSIX;
        # Windows Git Bash must fail closed at the platform guard by design.
        if os.name != 'nt':
            subprocess.run([bash,str(launcher_path)],input='n\n',text=True,check=True,cwd=root)


def test_first_install_does_not_reuse_old_runtime_as_bootstrap_prerequisite():
    root=Path(__file__).resolve().parents[1]
    implementation=(root/'deployment/install_chief_xubuntu.sh').read_text(encoding='utf-8')
    assert '/home/ape-xubuntu/chief-runtime' not in implementation
    assert '/home/ape-xubuntu/chief-agent' not in implementation
    assert 'PYTHON="$RUNTIME/bin/python"' in implementation
    assert 'git clone --no-local "$REPO_URL" "$TOOLING"' in implementation
