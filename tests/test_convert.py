from __future__ import annotations

import gzip
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from arc_to_reitti.convert import (
    MAX_NESTED_ZIP_DEPTH,
    ConversionError,
    EmptyArchiveError,
    MissingWeeksError,
    convert_archive,
    convert_week,
)


def sample(**values):
    base = {"date": "2026-09-28T10:00:00Z", "latitude": 55.9, "longitude": -3.2}
    base.update(values)
    return base


def zip_bytes(weeks: dict[str, dict], prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for period, data in weeks.items():
            archive.writestr(
                f"{prefix}{period}.json.gz",
                gzip.compress(json.dumps(data).encode("utf-8")),
            )
    return buffer.getvalue()


def write_archive(
    folder: Path, weeks: dict[str, dict], prefix: str = "", name: str = "export.zip"
) -> Path:
    path = folder / name
    path.write_bytes(zip_bytes(weeks, prefix))
    return path


def write_week_file(
    folder: Path, period: str, data: dict, compressed: bool = True
) -> Path:
    suffix = ".json.gz" if compressed else ".json"
    payload = json.dumps(data).encode("utf-8")
    path = folder / f"{period}{suffix}"
    path.write_bytes(gzip.compress(payload) if compressed else payload)
    return path


class ConvertWeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_converts_and_deduplicates_samples(self):
        data = {
            "samples": [
                sample(horizontalAccuracy=20, altitude=50),
                sample(horizontalAccuracy=10, altitude=55),
                sample(horizontalAccuracy=30, altitude=60),
                sample(date="2026-09-28T10:00:01Z", latitude=55.91, longitude=-3.21),
                sample(disabled=True),
                sample(latitude=999),
                sample(date=None),
                sample(date="not-a-date"),
                "not a sample",
            ]
        }
        result = convert_week(data, "2026-W40", self.out)

        self.assertEqual(result["samples"], 9)
        self.assertEqual(result["points"], 2)
        self.assertEqual(result["duplicate_timestamps"], 2)
        self.assertEqual(result["skipped"], 5)
        self.assertEqual(result["first"], "2026-09-28T10:00:00Z")
        self.assertEqual(result["last"], "2026-09-28T10:00:01Z")

        payload = json.loads((self.out / "2026-W40.geojson").read_text())
        self.assertEqual(payload["type"], "FeatureCollection")
        self.assertEqual(payload["name"], "Arc Timeline 2026-W40")
        first, second = payload["features"]
        self.assertEqual(first["geometry"]["coordinates"], [-3.2, 55.9, 55.0])
        self.assertEqual(first["properties"]["accuracy"], 10.0)
        self.assertEqual(first["properties"]["elevation"], 55.0)
        self.assertEqual(second["geometry"]["coordinates"], [-3.21, 55.91])
        self.assertEqual(second["properties"]["accuracy"], 50.0)
        self.assertNotIn("elevation", second["properties"])

    def test_empty_week_is_written(self):
        result = convert_week({"samples": []}, "2018-W28", self.out)
        self.assertEqual(result["points"], 0)
        self.assertIsNone(result["first"])
        payload = json.loads((self.out / "2018-W28.geojson").read_text())
        self.assertEqual(payload["features"], [])

    def test_timestamps_are_normalised_to_utc(self):
        data = {
            "samples": [
                sample(date="2026-09-28T12:00:00+02:00"),
                sample(date="2026-09-28T09:00:00Z", latitude=55.8),
            ]
        }
        result = convert_week(data, "2026-W40", self.out)
        self.assertEqual(result["first"], "2026-09-28T09:00:00Z")
        self.assertEqual(result["last"], "2026-09-28T10:00:00Z")

    def test_fractional_seconds_sort_after_whole_seconds(self):
        data = {
            "samples": [
                sample(date="2026-09-28T10:00:00.500Z"),
                sample(date="2026-09-28T10:00:00Z", latitude=55.8),
            ]
        }
        result = convert_week(data, "2026-W40", self.out)
        self.assertEqual(result["first"], "2026-09-28T10:00:00Z")
        self.assertEqual(result["last"], "2026-09-28T10:00:00.500000Z")

    def test_missing_samples_is_an_empty_week(self):
        result = convert_week({}, "2026-W40", self.out)
        self.assertEqual(result["points"], 0)

    def test_samples_must_be_a_list(self):
        with self.assertRaises(ConversionError):
            convert_week({"samples": "bad"}, "2026-W40", self.out)


class ConvertArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def test_converts_selected_week_and_flattens_nested_paths(self):
        archive = write_archive(
            self.root,
            {"2026-W40": {"samples": [sample()]}, "2026-W41": {"samples": [sample()]}},
            prefix="Arc Export/",
        )
        result = convert_archive(archive, self.out, weeks=["2026-W40"])

        self.assertEqual(result["manifest"]["totals"]["weeks"], 1)
        self.assertEqual(result["manifest"]["totals"]["points"], 1)
        self.assertTrue((self.out / "2026-W40.geojson").exists())
        self.assertFalse((self.out / "2026-W41.geojson").exists())
        self.assertEqual(result["manifest"]["source"], "export.zip")
        self.assertTrue((self.out / "manifest.json").exists())

    def test_missing_week_is_reported(self):
        archive = write_archive(self.root, {"2026-W40": {"samples": []}})
        with self.assertRaises(MissingWeeksError):
            convert_archive(archive, self.out, weeks=["2026-W41"])

    def test_archive_without_weeks_is_reported(self):
        path = self.root / "empty.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "not an Arc export")
        with self.assertRaises(EmptyArchiveError):
            convert_archive(path, self.out)

    def test_missing_archive_is_reported(self):
        with self.assertRaises(ConversionError):
            convert_archive(self.root / "missing.zip", self.out)

    def test_corrupt_week_is_reported(self):
        path = self.root / "corrupt.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("2026-W40.json.gz", b"not gzip")
        with self.assertRaises(ConversionError):
            convert_archive(path, self.out)

    def test_duplicate_week_is_combined(self):
        path = self.root / "duplicate.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for prefix in ("a/", "b/"):
                archive.writestr(
                    f"{prefix}2026-W40.json.gz",
                    gzip.compress(
                        json.dumps({"samples": [sample(horizontalAccuracy=20)]}).encode("utf-8")
                    ),
                )
        result = convert_archive(path, self.out)
        week = result["manifest"]["weeks"][0]
        self.assertEqual(week["samples"], 2)
        self.assertEqual(week["points"], 1)
        self.assertEqual(week["duplicate_timestamps"], 1)
        self.assertEqual(len(week["sources"]), 2)

    def test_single_weekly_file(self):
        path = write_week_file(self.root, "2026-W40", {"samples": [sample()]})
        result = convert_archive(path, self.out)
        self.assertEqual(result["manifest"]["totals"]["weeks"], 1)
        self.assertEqual(result["manifest"]["totals"]["points"], 1)

    def test_directory_of_weekly_files(self):
        folder = self.root / "weeklies"
        folder.mkdir()
        write_week_file(folder, "2026-W40", {"samples": [sample()]})
        write_week_file(folder, "2026-W41", {"samples": [sample()]}, compressed=False)
        result = convert_archive(folder, self.out)
        self.assertEqual(result["manifest"]["totals"]["weeks"], 2)
        self.assertTrue((self.out / "2026-W41.geojson").exists())

    def test_directory_of_zip_archives(self):
        folder = self.root / "zips"
        folder.mkdir()
        write_archive(folder, {"2026-W40": {"samples": [sample()]}}, name="one.zip")
        write_archive(folder, {"2026-W41": {"samples": [sample()]}}, name="two.zip")
        result = convert_archive(folder, self.out)
        self.assertEqual(result["manifest"]["totals"]["weeks"], 2)

    def test_zip_of_zip_archives(self):
        outer = self.root / "all-exports.zip"
        with zipfile.ZipFile(outer, "w") as archive:
            archive.writestr("export-one.zip", zip_bytes({"2026-W40": {"samples": [sample()]}}))
            archive.writestr(
                "nested/export-two.zip",
                zip_bytes({"2026-W41": {"samples": [sample()]}}),
            )
        result = convert_archive(outer, self.out)
        self.assertEqual(result["manifest"]["totals"]["weeks"], 2)

    def test_combines_a_week_from_multiple_containers(self):
        folder = self.root / "mixed"
        folder.mkdir()
        write_week_file(folder, "2026-W40", {"samples": [sample(horizontalAccuracy=20)]})
        write_archive(
            folder,
            {"2026-W40": {"samples": [sample(horizontalAccuracy=10)]}},
            name="extra.zip",
        )
        result = convert_archive(folder, self.out)
        week = result["manifest"]["weeks"][0]
        self.assertEqual(week["samples"], 2)
        self.assertEqual(week["points"], 1)
        self.assertEqual(week["duplicate_timestamps"], 1)
        self.assertEqual(len(week["sources"]), 2)
        payload = json.loads((self.out / "2026-W40.geojson").read_text())
        self.assertEqual(payload["features"][0]["properties"]["accuracy"], 10.0)

    def test_nested_zip_depth_is_limited(self):
        payload = zip_bytes({"2026-W40": {"samples": []}})
        for _ in range(MAX_NESTED_ZIP_DEPTH + 1):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("nested.zip", payload)
            payload = buffer.getvalue()
        path = self.root / "deep.zip"
        path.write_bytes(payload)
        with self.assertRaises(ConversionError):
            convert_archive(path, self.out)

    def test_directory_without_weeks_is_reported(self):
        folder = self.root / "nothing"
        folder.mkdir()
        (folder / "readme.txt").write_text("not an Arc export")
        with self.assertRaises(EmptyArchiveError):
            convert_archive(folder, self.out)

    def test_non_weekly_file_is_rejected(self):
        path = self.root / "notes.txt"
        path.write_text("not an Arc export")
        with self.assertRaises(ConversionError):
            convert_archive(path, self.out)

    def test_unsafe_archive_path_is_reported(self):
        path = self.root / "unsafe.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "../2026-W40.json.gz",
                gzip.compress(json.dumps({"samples": []}).encode("utf-8")),
            )
        with self.assertRaises(ConversionError):
            convert_archive(path, self.out)

    def test_progress_callback_is_called(self):
        archive = write_archive(self.root, {"2026-W40": {"samples": [sample()]}})
        calls = []
        convert_archive(archive, self.out, progress=lambda index, total, result: calls.append((index, total, result["period"])))
        self.assertEqual(calls, [(1, 1, "2026-W40")])


if __name__ == "__main__":
    unittest.main()
