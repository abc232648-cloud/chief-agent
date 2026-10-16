"""Read-only dependency update discovery with private, deduplicated observations.

This module never downloads, installs, repins, restarts, or activates software.
It only compares trusted public release metadata with the versions Chief is
currently configured to use or can observe in its own Python environment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlparse
from urllib.request import Request
from browser.https_transport import urlopen


_POLICY = "config/dependency_update_policy.json"
_STATE = "maintenance/dependency-updates.json"
_MAX_RESPONSE_BYTES = 512 * 1024
_ALLOWED_HOSTS = {"api.github.com", "pypi.org"}
_PACKAGE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)")
_STABLE_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)$")


class DependencyUpdateError(RuntimeError):
    pass


class DependencyUpdateStateError(DependencyUpdateError):
    pass


def _json_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _plain_directory(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Dependency update state root must be absolute.")
    if path.is_symlink():
        raise DependencyUpdateStateError("Dependency update state root cannot be a symlink.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise DependencyUpdateStateError("Dependency update state root is not a directory.")
    return path


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_no_duplicates)


def load_policy(source_root, environ=None):
    source_root = Path(source_root)
    data = _load_json(source_root / _POLICY)
    expected = {
        "format_version", "cadence_env", "default_cadence_hours",
        "minimum_cadence_hours", "maximum_cadence_hours", "runtime_sources",
        "python_requirement_files", "python_index_url",
    }
    if not isinstance(data, dict) or set(data) != expected or data["format_version"] != 1:
        raise ValueError("Dependency update policy has an invalid shape.")
    minimum = data["minimum_cadence_hours"]
    maximum = data["maximum_cadence_hours"]
    default = data["default_cadence_hours"]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (minimum, maximum, default)):
        raise ValueError("Dependency update cadence bounds must be integers.")
    if not (1 <= minimum <= default <= maximum <= 24 * 365):
        raise ValueError("Dependency update cadence bounds are invalid.")
    env_name = data["cadence_env"]
    if not isinstance(env_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", env_name):
        raise ValueError("Dependency update cadence environment key is invalid.")
    raw = (environ if environ is not None else os.environ).get(env_name)
    try:
        cadence = default if raw in (None, "") else int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{env_name} must be an integer number of hours.") from exc
    if not minimum <= cadence <= maximum:
        raise ValueError(f"{env_name} must be between {minimum} and {maximum} hours.")
    data = dict(data)
    data["cadence_hours"] = cadence
    return data


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Dependency metadata URL must be credential-free HTTPS.")
    if parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError("Dependency metadata host is not allow-listed.")


def fetch_json(url: str, *, timeout: float = 10.0):
    _validate_url(url)
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Chief-Update-Discovery/1"})
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        _validate_url(final_url)
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise DependencyUpdateError("Dependency metadata response exceeded the size limit.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_json_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DependencyUpdateError("Dependency metadata response was not valid JSON.") from exc


def _version_tuple(value):
    if not isinstance(value, str):
        return None
    match = _STABLE_VERSION.fullmatch(value.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(current, latest):
    left = _version_tuple(current)
    right = _version_tuple(latest)
    if left is None or right is None:
        return "UNORDERED"
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    if right > left:
        return "UPDATE_AVAILABLE"
    if right == left:
        return "CURRENT"
    return "LOCAL_NEWER"


def _candidate_versions(source_root: Path):
    data = _load_json(source_root / "config/runtime_candidates.json")
    rows = data.get("candidates") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("Runtime candidate catalog has an invalid shape.")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("version"), str):
            raise ValueError("Runtime candidate catalog contains an invalid candidate.")
        if row["id"] in result:
            raise ValueError("Runtime candidate catalog contains duplicate candidates.")
        result[row["id"]] = row["version"]
    return result


def _runtime_observations(source_root, policy, fetcher):
    pinned = _candidate_versions(source_root)
    observations = []
    for source in policy["runtime_sources"]:
        try:
            if not isinstance(source, dict) or set(source) != {"candidate_id", "url", "version_field", "version_regex"}:
                raise ValueError("Runtime update source has an invalid shape.")
            candidate_id = source["candidate_id"]
            current = pinned[candidate_id]
            payload = fetcher(source["url"])
            value = payload.get(source["version_field"]) if isinstance(payload, dict) else None
            match = re.search(source["version_regex"], value or "")
            if not match:
                raise ValueError("Release metadata did not contain the expected version field.")
            latest = match.group(1)
            status = compare_versions(current, latest)
            observations.append({
                "kind": "runtime", "name": candidate_id, "current": current,
                "latest": latest, "status": status,
            })
        except Exception:
            observations.append({
                "kind": "runtime", "name": source.get("candidate_id", "unknown") if isinstance(source, dict) else "unknown",
                "current": pinned.get(source.get("candidate_id"), None) if isinstance(source, dict) else None,
                "latest": None, "status": "SOURCE_ERROR",
            })
    return observations


def _requirements(source_root: Path, filenames):
    names = []
    for filename in filenames:
        path = source_root / filename
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith(("-r", "--")):
                continue
            match = _PACKAGE.match(line)
            if not match:
                raise ValueError(f"Unsupported requirement line in {filename}.")
            canonical = match.group(1).replace("_", "-").lower()
            if canonical not in names:
                names.append(canonical)
    return tuple(names)


def _python_observations(source_root, policy, fetcher, installed_version):
    observations = []
    for name in _requirements(source_root, policy["python_requirement_files"]):
        try:
            current = installed_version(name)
        except metadata.PackageNotFoundError:
            observations.append({"kind": "python", "name": name, "current": None, "latest": None, "status": "NOT_INSTALLED_HERE"})
            continue
        except Exception:
            observations.append({"kind": "python", "name": name, "current": None, "latest": None, "status": "LOCAL_VERSION_ERROR"})
            continue
        try:
            url = policy["python_index_url"].format(name=quote(name, safe=""))
            payload = fetcher(url)
            latest = payload.get("info", {}).get("version") if isinstance(payload, dict) else None
            status = compare_versions(current, latest)
            observations.append({"kind": "python", "name": name, "current": current, "latest": latest, "status": status})
        except Exception:
            observations.append({"kind": "python", "name": name, "current": current, "latest": None, "status": "SOURCE_ERROR"})
    return observations


def _state_path(state_root):
    root = _plain_directory(Path(state_root))
    maintenance = root / "maintenance"
    if maintenance.is_symlink():
        raise DependencyUpdateStateError("Maintenance state directory cannot be a symlink.")
    maintenance.mkdir(mode=0o700, exist_ok=True)
    return maintenance / "dependency-updates.json"


def _read_state(path):
    if not path.exists() and not path.is_symlink():
        return {"format_version": 1, "last_checked_at": None, "notified_update_keys": []}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_RESPONSE_BYTES:
        raise DependencyUpdateStateError("Dependency update state file is unsafe.")
    try:
        data = _load_json(path)
    except Exception as exc:
        raise DependencyUpdateStateError("Dependency update state file is invalid.") from exc
    expected = {"format_version", "last_checked_at", "notified_update_keys", "observations"}
    if not isinstance(data, dict) or not set(data).issubset(expected) or data.get("format_version") != 1:
        raise DependencyUpdateStateError("Dependency update state has an invalid shape.")
    notified = data.get("notified_update_keys", [])
    if not isinstance(notified, list) or any(not isinstance(item, str) for item in notified) or len(set(notified)) != len(notified):
        raise DependencyUpdateStateError("Dependency update notification state is invalid.")
    if data.get("last_checked_at") is not None and not isinstance(data.get("last_checked_at"), str):
        raise DependencyUpdateStateError("Dependency update timestamp is invalid.")
    return data


def _write_state(path: Path, data):
    encoded = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.chmod(0o700)
    fd, temp_name = tempfile.mkstemp(prefix=".dependency-updates-", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(temp_name, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _parse_checked_at(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DependencyUpdateStateError("Dependency update timestamp is invalid.") from exc
    if parsed.tzinfo is None:
        raise DependencyUpdateStateError("Dependency update timestamp is not timezone-aware.")
    return parsed.astimezone(timezone.utc)


def check_if_due(source_root, state_root, *, environ=None, now=None, fetcher=fetch_json, installed_version=metadata.version):
    """Check for newer versions when due and persist only sanitized observations."""
    source_root = Path(source_root).resolve()
    policy = load_policy(source_root, environ=environ)
    path = _state_path(Path(state_root).resolve())
    state = _read_state(path)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    previous = _parse_checked_at(state.get("last_checked_at"))
    if previous is not None and current_time < previous + timedelta(hours=policy["cadence_hours"]):
        return {
            "status": "NOT_DUE", "cadence_hours": policy["cadence_hours"],
            "last_checked_at": previous.isoformat().replace("+00:00", "Z"),
            "updates": [], "new_updates": [], "errors": 0,
        }

    observations = _runtime_observations(source_root, policy, fetcher)
    observations.extend(_python_observations(source_root, policy, fetcher, installed_version))
    updates = [item for item in observations if item["status"] == "UPDATE_AVAILABLE"]
    keys = [f"{item['kind']}:{item['name']}:{item['current']}->{item['latest']}" for item in updates]
    notified = set(state.get("notified_update_keys", []))
    new_keys = set(keys) - notified
    new_updates = [item for item, key in zip(updates, keys) if key in new_keys]
    errors = sum(item["status"].endswith("ERROR") for item in observations)
    checked_at = current_time.isoformat().replace("+00:00", "Z")
    _write_state(path, {
        "format_version": 1,
        "last_checked_at": checked_at,
        "notified_update_keys": sorted(notified | set(keys)),
        "observations": observations,
    })
    return {
        "status": "CHECKED_WITH_ERRORS" if errors else "CHECKED",
        "cadence_hours": policy["cadence_hours"], "last_checked_at": checked_at,
        "updates": updates, "new_updates": new_updates, "errors": errors,
    }
