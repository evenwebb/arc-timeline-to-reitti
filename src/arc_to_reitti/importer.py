"""Upload converted GeoJSON files to Reitti's GeoJSON import API."""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable


class ReittiImportError(Exception):
    """Reitti rejected an upload or could not be reached."""


def _safe_filename(name: str) -> str:
    return name.replace('"', "_").replace("\r", "_").replace("\n", "_")


def _multipart(device: int, file_path: Path) -> tuple[bytes, str]:
    boundary = "----arc-to-reitti-" + secrets.token_hex(16)
    filename = _safe_filename(file_path.name)
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    field("device", str(device))
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/geo+json\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post_file(
    base_url: str,
    token: str,
    device: int,
    file_path: Path,
    timeout: float = 120.0,
) -> dict:
    """Upload one GeoJSON file and return Reitti's JSON response."""
    url = base_url.rstrip("/") + "/api/v1/geojson/import"
    body, content_type = _multipart(device, file_path)
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "X-API-Token": token,
            "Content-Type": content_type,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace").strip()
        raise ReittiImportError(f"{file_path.name}: HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReittiImportError(f"{file_path.name}: {exc}") from exc

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ReittiImportError(f"{file_path.name}: response was not JSON") from exc
    if not isinstance(result, dict) or result.get("success") is not True:
        message = result.get("message") if isinstance(result, dict) else None
        raise ReittiImportError(f"{file_path.name}: {message or result!r}")
    return result


def read_log(path: Path) -> set[str]:
    """Return files already recorded as successfully imported or skipped."""
    done: set[str] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, payload = line.partition(" ")
        if not separator:
            continue
        try:
            result = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and result.get("success") is True:
            done.add(name)
    return done


def is_empty(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("features") == []


def _append_log(path: Path, name: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{name} {json.dumps(result, separators=(',', ':'))}\n")


def import_directory(
    directory: Path,
    base_url: str,
    token: str,
    device: int = 1,
    log_path: Path | None = None,
    retries: int = 3,
    timeout: float = 120.0,
    progress: Callable[[int, int, Path, str], None] | None = None,
) -> dict:
    """Upload every .geojson file in a directory, skipping files in the log."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ReittiImportError(f"directory not found: {directory}")
    if not token:
        raise ReittiImportError("an API token is required")
    if retries < 0:
        raise ReittiImportError("retries cannot be negative")

    files = sorted(directory.glob("*.geojson"))
    if not files:
        raise ReittiImportError(f"no .geojson files found in {directory}")

    log_path = Path(log_path) if log_path is not None else directory / "import.log"
    done = read_log(log_path)
    imported = 0
    already_imported = 0
    empty = 0

    for index, path in enumerate(files, 1):
        if path.name in done:
            already_imported += 1
            if progress is not None:
                progress(index, len(files), path, "already imported")
            continue
        if is_empty(path):
            _append_log(log_path, path.name, {"success": True, "skipped": "empty"})
            empty += 1
            if progress is not None:
                progress(index, len(files), path, "empty")
            continue

        for attempt in range(retries + 1):
            try:
                result = post_file(base_url, token, device, path, timeout=timeout)
                break
            except ReittiImportError as exc:
                if attempt >= retries:
                    _append_log(log_path, path.name, {"success": False, "error": str(exc)})
                    raise
                time.sleep(2**attempt)

        _append_log(log_path, path.name, result)
        imported += 1
        if progress is not None:
            progress(index, len(files), path, "imported")

    return {
        "files": len(files),
        "imported": imported,
        "already_imported": already_imported,
        "empty": empty,
        "log": str(log_path),
    }
