"""Convert Arc Timeline weekly JSON exports into Reitti-compatible GeoJSON."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

PERIOD_RE = re.compile(r"^\d{4}-W\d{2}$")
SUFFIX = ".json.gz"
DEFAULT_ACCURACY_M = 50.0


class ConversionError(Exception):
    """The archive or one of its weeks cannot be converted."""


class MissingWeeksError(ConversionError):
    """One or more requested weeks are not present in the archive."""


class EmptyArchiveError(ConversionError):
    """The archive contains no weekly Arc Timeline files."""


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _parse_timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _format_timestamp(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def discover_members(archive: zipfile.ZipFile) -> dict[str, str]:
    """Return period -> zip member for every weekly Arc file in the archive."""
    members: dict[str, str] = {}
    for name in archive.namelist():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ConversionError(f"unsafe path in archive: {name}")
        if any(part == "__MACOSX" for part in path.parts):
            continue
        if path.name.startswith("._") or not path.name.endswith(SUFFIX):
            continue
        period = path.name[: -len(SUFFIX)]
        if not PERIOD_RE.match(period):
            continue
        previous = members.get(period)
        if previous is not None:
            raise ConversionError(f"duplicate week {period} in {previous} and {name}")
        members[period] = name
    return members


def _load_week(archive: zipfile.ZipFile, member: str) -> dict:
    try:
        with archive.open(member) as raw:
            payload = gzip.decompress(raw.read())
        data = json.loads(payload)
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise ConversionError(f"{member}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConversionError(f"{member}: expected a JSON object")
    return data


def convert_week(data: dict, period: str, out_dir: Path) -> dict:
    """Convert one week's decoded JSON to a GeoJSON file and return its report."""
    samples = data.get("samples", [])
    if samples is None:
        samples = []
    if not isinstance(samples, list):
        raise ConversionError(f"{period}: 'samples' is not a list")

    by_time: dict[str, tuple[float, dict, dt.datetime]] = {}
    skipped = 0
    duplicates = 0

    for sample in samples:
        if not isinstance(sample, dict):
            skipped += 1
            continue
        if sample.get("disabled"):
            skipped += 1
            continue
        lat = sample.get("latitude")
        lon = sample.get("longitude")
        if not (_finite(lat) and _finite(lon)):
            skipped += 1
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skipped += 1
            continue
        parsed = _parse_timestamp(sample.get("date"))
        if parsed is None:
            skipped += 1
            continue
        stamp = _format_timestamp(parsed)

        accuracy = sample.get("horizontalAccuracy")
        accuracy = float(accuracy) if _finite(accuracy) and accuracy > 0 else DEFAULT_ACCURACY_M
        previous = by_time.get(stamp)
        if previous is not None:
            duplicates += 1
            if accuracy >= previous[0]:
                continue
        by_time[stamp] = (accuracy, sample, parsed)

    features = []
    for stamp, (accuracy, sample, _) in sorted(by_time.items(), key=lambda item: item[1][2]):
        elevation = sample.get("altitude")
        has_elevation = _finite(elevation)
        coordinates = [sample["longitude"], sample["latitude"]]
        if has_elevation:
            coordinates.append(float(elevation))
        properties = {"timestamp": stamp, "accuracy": accuracy}
        if has_elevation:
            properties["elevation"] = float(elevation)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coordinates},
                "properties": properties,
            }
        )

    output = {
        "type": "FeatureCollection",
        "name": f"Arc Timeline {period}",
        "features": features,
    }
    filename = f"{period}.geojson"
    _atomic_write_text(
        out_dir / filename,
        json.dumps(output, ensure_ascii=False, separators=(",", ":")),
    )
    return {
        "period": period,
        "file": filename,
        "samples": len(samples),
        "points": len(features),
        "duplicate_timestamps": duplicates,
        "skipped": skipped,
        "first": features[0]["properties"]["timestamp"] if features else None,
        "last": features[-1]["properties"]["timestamp"] if features else None,
    }


def convert_archive(
    archive_path: Path,
    out_dir: Path,
    weeks: Iterable[str] | None = None,
    progress: Callable[[int, int, dict], None] | None = None,
) -> dict:
    """Convert a weekly Arc Timeline zip archive into a directory of GeoJSON files."""
    archive_path = Path(archive_path)
    out_dir = Path(out_dir)
    if not archive_path.is_file():
        raise ConversionError(f"archive not found: {archive_path}")

    selected_weeks = None
    if weeks is not None:
        selected_weeks = sorted({week.strip() for week in weeks if week.strip()})
        if not selected_weeks:
            raise ConversionError("--weeks selected no weeks")
        invalid = [week for week in selected_weeks if not PERIOD_RE.match(week)]
        if invalid:
            raise ConversionError(f"invalid week ids: {', '.join(invalid)}")

    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ConversionError(f"cannot open {archive_path}: {exc}") from exc

    with archive:
        members = discover_members(archive)
        if selected_weeks is not None:
            missing = sorted(set(selected_weeks) - set(members))
            if missing:
                raise MissingWeeksError(f"weeks not in archive: {', '.join(missing)}")
            chosen = {period: members[period] for period in selected_weeks}
        else:
            chosen = dict(sorted(members.items()))
        if not chosen:
            raise EmptyArchiveError(f"no weekly .json.gz files found in {archive_path}")

        results = []
        for index, (period, member) in enumerate(chosen.items(), 1):
            result = convert_week(_load_week(archive, member), period, out_dir)
            results.append(result)
            if progress is not None:
                progress(index, len(chosen), result)

    totals = {
        "weeks": len(results),
        "samples": sum(item["samples"] for item in results),
        "points": sum(item["points"] for item in results),
        "duplicate_timestamps": sum(item["duplicate_timestamps"] for item in results),
        "skipped": sum(item["skipped"] for item in results),
    }
    manifest = {
        "generated": dt.datetime.now(tz=dt.timezone.utc).isoformat(timespec="seconds"),
        "source": archive_path.name,
        "totals": totals,
        "weeks": results,
    }
    manifest_path = out_dir / "manifest.json"
    _atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return {"manifest": manifest, "manifest_path": manifest_path}
