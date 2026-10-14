"""Stable operator-provisioned entrypoint selecting verified Chief source slots."""
import argparse
import os


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
    os.execve(python, command, dict(os.environ))


if __name__ == '__main__':
    main()
