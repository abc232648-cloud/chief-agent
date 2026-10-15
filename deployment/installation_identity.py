"""Cross-platform Chief installation identity and network naming.

The installer asks for a human owner/organisation name. Networking details are
then derived here so Linux, Windows and future GUI installers share one rule.
Names are display metadata; generated identifiers remain stable when names change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import uuid

from deployment.https_policy import public_hostname

SCHEMA = "chief-installation-identity-v1"
MAX_DISPLAY_NAME = 120
_DNS_LABEL_LIMIT = 63
_PREFIX = "chief-"


def display_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Chief owner/organisation name is required.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Chief owner/organisation name contains unsupported control characters.")
    name = " ".join(value.split())
    if not name:
        raise ValueError("Chief owner/organisation name is required.")
    if len(name) > MAX_DISPLAY_NAME:
        raise ValueError(f"Chief owner/organisation name must be at most {MAX_DISPLAY_NAME} characters.")
    return name


def _slug(value: str, *, limit: int) -> str:
    name = display_name(value)
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        slug = "owner-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    slug = slug[:limit].rstrip("-")
    if not slug:
        raise ValueError("Chief could not derive a safe local network name.")
    return slug


def local_host_label(owner_name: str) -> str:
    slug = _slug(owner_name, limit=_DNS_LABEL_LIMIT - len(_PREFIX))
    label = _PREFIX + slug
    if len(label) > _DNS_LABEL_LIMIT:
        raise AssertionError("Derived Chief host label exceeded the DNS label limit.")
    return label


def local_hostname(owner_name: str) -> str:
    return public_hostname(local_host_label(owner_name) + ".local")


def new_identity(
    owner_name: str,
    *,
    hostname: str | None = None,
    local_managed: bool | None = None,
) -> dict:
    owner = display_name(owner_name)
    if local_managed is None:
        local_managed = hostname is None
    if hostname is None:
        if not local_managed:
            raise ValueError("A public hostname is required when Chief does not manage local naming.")
        host = local_hostname(owner)
    else:
        host = public_hostname(hostname)
        if local_managed and not host.endswith(".local"):
            raise ValueError("A Chief-managed local hostname must use the .local mDNS domain.")
    return {
        "schema": SCHEMA,
        "chief_id": uuid.uuid4().hex,
        "owner_id": uuid.uuid4().hex,
        "owner_display_name": owner,
        "network": {
            "hostname": host,
            "mode": "LOCAL_MDNS" if local_managed else "PUBLIC_DNS",
            "managed_from_owner_name": bool(local_managed),
        },
    }


def rename_plan(identity: dict, new_owner_name: str) -> dict:
    """Plan a rename without silently changing the HTTPS origin.

    The owner display name can always change. If Chief manages the local mDNS
    address, a new hostname is proposed and must be explicitly applied by the
    settings/update layer because changing an origin affects installed PWAs,
    browser storage, push subscriptions and bookmarks.
    """
    if not isinstance(identity, dict) or identity.get("schema") != SCHEMA:
        raise ValueError("Unsupported Chief installation identity.")
    owner = display_name(new_owner_name)
    network = identity.get("network") or {}
    current = public_hostname(network.get("hostname"))
    proposed = local_hostname(owner) if network.get("managed_from_owner_name") else current
    return {
        "owner_display_name": owner,
        "current_hostname": current,
        "proposed_hostname": proposed,
        "hostname_change": proposed != current,
        "requires_origin_migration": proposed != current,
    }


def write_identity(path: str | Path, identity: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(identity, sort_keys=True, indent=2) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(target, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Derive and persist Chief installation identity.")
    parser.add_argument("--owner-name", required=True)
    parser.add_argument("--output")
    names = parser.add_mutually_exclusive_group()
    names.add_argument("--public-host")
    names.add_argument("--local-host")
    args = parser.parse_args(argv)
    if args.local_host:
        identity = new_identity(args.owner_name, hostname=args.local_host, local_managed=True)
    else:
        identity = new_identity(args.owner_name, hostname=args.public_host)
    if args.output:
        write_identity(args.output, identity)
    print(identity["network"]["hostname"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
