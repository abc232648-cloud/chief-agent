"""Generate a candidate maintained-runtime hash lock from an observed inventory.

Temporary release-engineering helper for the Job PWA Web Push dependency refresh.
It preserves every package/version in the selected inventory, adds the exact
pywebpush release, and asks pip-tools to generate the complete hash set.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--webpush", default="2.5.0")
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    packages = inventory.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise SystemExit("inventory packages are missing")

    # pip itself is runtime tooling, not an application dependency in the lock.
    requested = {
        str(name): str(version)
        for name, version in packages.items()
        if str(name).lower() != "pip"
    }
    requested["pywebpush"] = args.webpush

    source = args.output.with_suffix(".in")
    source.write_text(
        "# Generated from maintained runtime inventory; do not edit by hand.\n"
        + "\n".join(f"{name}=={requested[name]}" for name in sorted(requested, key=str.lower))
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "piptools",
            "compile",
            "--generate-hashes",
            "--resolver=backtracking",
            "--output-file",
            str(args.output),
            str(source),
        ],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
