"""Promote verified Web Push lock candidates into maintained release manifests."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^ \\]+)")


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_packages(path: Path) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = NAME_RE.match(line)
        if match:
            out[normalized(match.group(1))] = (match.group(1), match.group(2))
    if "pywebpush" not in out:
        raise SystemExit(f"{path}: pywebpush missing from candidate lock")
    return out


def promote(candidate: Path, destination: Path, inventory_path: Path) -> None:
    packages = lock_packages(candidate)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    current = inventory.get("packages")
    if not isinstance(current, dict):
        raise SystemExit(f"{inventory_path}: packages map missing")

    by_normalized = {normalized(name): name for name in current}
    for key, existing_name in by_normalized.items():
        candidate_item = packages.get(key)
        if candidate_item is None:
            raise SystemExit(f"{candidate}: existing package disappeared: {existing_name}")
        if str(current[existing_name]) != candidate_item[1]:
            raise SystemExit(
                f"{candidate}: unrelated version drift for {existing_name}: "
                f"{current[existing_name]} -> {candidate_item[1]}"
            )

    for key, (candidate_name, version) in sorted(packages.items()):
        if key not in by_normalized:
            current[candidate_name] = version

    destination.write_bytes(candidate.read_bytes())
    inventory_path.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ubuntu", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    args = parser.parse_args()

    promote(args.ubuntu, Path("release/ubuntu-hashed.txt"), Path("release/ubuntu-inventory.json"))
    promote(args.windows, Path("release/windows-hashed.txt"), Path("release/windows-inventory.json"))

    requirements = Path("requirements.txt")
    text = requirements.read_text(encoding="utf-8")
    text, count = re.subn(r"^pywebpush[^\n]*$", "pywebpush==2.5.0", text, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit("requirements.txt must contain exactly one pywebpush requirement")
    requirements.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
