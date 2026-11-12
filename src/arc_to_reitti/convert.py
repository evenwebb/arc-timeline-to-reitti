"""Convert Arc Timeline weekly JSON exports into Reitti-compatible GeoJSON."""

from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

PERIOD_RE = re.compile(r"^\d{4}-W\d{2}$")
WEEK_SUFFIXES = (".json.gz", ".json")
MAX_NESTED_ZIP_DEPTH = 5
MAX_NESTED_ZIP_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_ACCURACY_M = 50.0


class ConversionError(Exception):
    """The input or one of its weeks cannot be converted."""


class MissingWeeksError(ConversionError):
    """One or more requested weeks are not present in the input."""


class EmptyArchiveError(ConversionError):
    """The input contains no weekly Arc Timeline files."""


@dataclass(frozen=True)
class WeeklySource:
    period: str
    label: str
    path: Path
    members: tuple[str, ...] = ()
    compressed: bool = True

    def read(self) -> bytes:
        if not self.members:
            return self.path.read_bytes()
        with zipfile.ZipFile(self.path) as archive:
            current = archive
            owned: list[zipfile.ZipFile] = []
            try:
                for member in self.members[:-1]:
                    nested = zipfile.ZipFile(io.BytesIO(current.read(member)))
                    owned.append(nested)
                    current = nested
                return current.read(self.members[-1])
            finally:
                for nested in reversed(owned):
                    nested.close()


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


def _week_from_name(name: str) -> tuple[str, bool] | None:
    base = PurePosixPath(name).name
    for suffix in WEEK_SUFFIXES:
        if base.endswith(suffix):
            period = base[: -len(suffix)]
            if PERIOD_RE.match(period):
                return period, suffix == ".json.gz"
    return None


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ConversionError(f"unsafe path in archive: {name}")
    return path


def _discover_zip(
    archive: zipfile.ZipFile,
    root: Path,
    label_prefix: str,
    chain: tuple[str, ...],
    depth: int,
    sources: list[WeeklySource],
    scratch: Path,
) -> None:
    if depth > MAX_NESTED_ZIP_DEPTH:
        raise ConversionError(f"nested zip depth exceeds {MAX_NESTED_ZIP_DEPTH}: {label_prefix}")

    for name in archive.namelist():
        if name.endswith("/"):
            continue
        path = _safe_member(name)
        if any(part == "__MACOSX" for part in path.parts):
            continue
        if path.name.startswith("._"):
            continue

        weekly = _week_from_name(name)
        if weekly is not None:
            period, compressed = weekly
            label = f"{label_prefix}:{'/'.join(chain + (name,))}"
            sources.append(
                WeeklySource(period, label, root, chain + (name,), compressed)
            )
            continue

        if path.name.lower().endswith(".zip"):
            info = archive.getinfo(name)
            if info.file_size > MAX_NESTED_ZIP_BYTES:
                raise ConversionError(f"nested zip is too large: {label_prefix}:{name}")
            with archive.open(name) as source, tempfile.NamedTemporaryFile(
                dir=scratch, suffix=".zip", delete=False
            ) as temporary:
                shutil.copyfileobj(source, temporary)
                extracted = Path(temporary.name)
            _discover_zip_path(
                extracted,
                sources,
                scratch,
                depth + 1,
                f"{label_prefix}:{name}",
            )


def _discover_zip_path(
    path: Path,
    sources: list[WeeklySource],
    scratch: Path,
    depth: int = 0,
    label_prefix: str | None = None,
) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            _discover_zip(
                archive,
                path,
                label_prefix or str(path),
                (),
                depth,
                sources,
                scratch,
            )
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ConversionError(f"cannot open {path}: {exc}") from exc


def _discover_directory(directory: Path, sources: list[WeeklySource], scratch: Path) -> None:
    for root, directories, files in os.walk(directory, followlinks=False):
        directories[:] = sorted(
            name for name in directories if not name.startswith(".") and name != "__MACOSX"
        )
        for name in sorted(files):
            if name.startswith("._"):
                continue
            path = Path(root) / name
            weekly = _week_from_name(name)
            if weekly is not None:
                period, compressed = weekly
                sources.append(WeeklySource(period, str(path), path, (), compressed))
            elif name.lower().endswith(".zip"):
                _discover_zip_path(path, sources, scratch)


def _discover_path(path: Path, scratch: Path) -> list[WeeklySource]:
    if path.is_dir():
        sources: list[WeeklySource] = []
        _discover_directory(path, sources, scratch)
        return sources
    if path.is_file():
        weekly = _week_from_name(path.name)
        if weekly is not None:
            period, compressed = weekly
            return [WeeklySource(period, str(path), path, (), compressed)]
        if path.name.lower().endswith(".zip"):
            sources = []
            _discover_zip_path(path, sources, scratch)
            return sources
        raise ConversionError(
            f"input must be a zip, a weekly .json.gz/.json file, or a directory: {path}"
        )
    raise ConversionError(f"input not found: {path}")


def _load_source(source: WeeklySource) -> dict:
    try:
        payload = source.read()
        if source.compressed:
            payload = gzip.decompress(payload)
        data = json.loads(payload)
    except (
        OSError,
        EOFError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        RuntimeError,
    ) as exc:
        raise ConversionError(f"{source.label}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConversionError(f"{source.label}: expected a JSON object")
    return data


def _load_samples(sources: list[WeeklySource]) -> tuple[list, list[str]]:
    samples: list = []
    labels: list[str] = []
    for source in sources:
        data = _load_source(source)
        week_samples = data.get("samples", [])
        if week_samples is None:
            week_samples = []
        if not isinstance(week_samples, list):
            raise ConversionError(f"{source.label}: 'samples' is not a list")
        samples.extend(week_samples)
        labels.append(source.label)
    return samples, labels


def convert_week(data: dict, period: str, out_dir: Path, sources: list[str] | None = None) -> dict:
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
        "sources": list(sources or []),
        "samples": len(samples),
        "points": len(features),
        "duplicate_timestamps": duplicates,
        "skipped": skipped,
        "first": features[0]["properties"]["timestamp"] if features else None,
        "last": features[-1]["properties"]["timestamp"] if features else None,
    }


def convert_archive(
    input_path: Path,
    out_dir: Path,
    weeks: Iterable[str] | None = None,
    progress: Callable[[int, int, dict], None] | None = None,
) -> dict:
    """Convert weekly Arc files from a zip, a directory or a single weekly file."""
    input_path = Path(input_path)
    out_dir = Path(out_dir)

    selected_weeks = None
    if weeks is not None:
        selected_weeks = sorted({week.strip() for week in weeks if week.strip()})
        if not selected_weeks:
            raise ConversionError("--weeks selected no weeks")
        invalid = [week for week in selected_weeks if not PERIOD_RE.match(week)]
        if invalid:
            raise ConversionError(f"invalid week ids: {', '.join(invalid)}")

    with tempfile.TemporaryDirectory(prefix="arc-to-reitti-") as scratch:
        discovered = _discover_path(input_path, Path(scratch))
        if not discovered:
            raise EmptyArchiveError(f"no weekly Arc files found in {input_path}")

        by_period: dict[str, list[WeeklySource]] = {}
        for source in discovered:
            by_period.setdefault(source.period, []).append(source)

        if selected_weeks is not None:
            missing = sorted(set(selected_weeks) - set(by_period))
            if missing:
                raise MissingWeeksError(f"weeks not in input: {', '.join(missing)}")
            chosen = {period: by_period[period] for period in selected_weeks}
        else:
            chosen = dict(sorted(by_period.items()))

        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for index, (period, sources) in enumerate(chosen.items(), 1):
            samples, labels = _load_samples(sources)
            result = convert_week({"samples": samples}, period, out_dir, sources=labels)
            results.append(result)
            if progress is not None:
                progress(index, len(chosen), result)

        totals = {
            "weeks": len(results),
            "sources": len(discovered),
            "samples": sum(item["samples"] for item in results),
            "points": sum(item["points"] for item in results),
            "duplicate_timestamps": sum(item["duplicate_timestamps"] for item in results),
            "skipped": sum(item["skipped"] for item in results),
        }
        manifest = {
            "generated": dt.datetime.now(tz=dt.timezone.utc).isoformat(timespec="seconds"),
            "source": input_path.resolve().name,
            "totals": totals,
            "weeks": results,
        }
        manifest_path = out_dir / "manifest.json"
        _atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        return {"manifest": manifest, "manifest_path": manifest_path}
