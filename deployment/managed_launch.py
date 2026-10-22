"""Stable operator-provisioned entrypoint selecting verified Chief source slots."""
import argparse
import os
import subprocess


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation', required=True)
    parser.add_argument('--component', choices=['dashboard', 'worker', 'scheduler'], required=True)
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args(argv)
    from update_center.managed import ManagedInstallation
    installation = ManagedInstallation(args.installation)
    identity = installation.selection(for_stop=args.stop)
    source, _ = installation.source(identity)
    python = installation.runtime(identity)
    command = [python, '-B', '-E', '-s', '-m', 'deployment.launch', '--component', args.component,
               '--config', installation.config['instance_config']]
    if args.stop:
        command.append('--stop')
    os.environ['CHIEF_MANAGED_INSTALLATION'] = str(installation.root)
    os.environ['CHIEF_MANAGED_SOURCE'] = identity
    os.chdir(source)
    if os.name == 'nt':
        # pythonw + CRT exec can crash before the service starts. Keep the
        # scheduled launcher alive and wait for the verified interpreter.
        # Task Scheduler retains ownership of this entire process tree.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        return subprocess.run(
            command, env=dict(os.environ), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup,
            check=False,
        ).returncode
    os.execve(python, command, dict(os.environ))


if __name__ == '__main__':
    raise SystemExit(main())
