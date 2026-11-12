from __future__ import annotations

import contextlib
import gzip
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from arc_to_reitti.cli import main


def write_archive(folder: Path) -> Path:
    path = folder / "export.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "2026-W40.json.gz",
            gzip.compress(
                json.dumps(
                    {
                        "samples": [
                            {
                                "date": "2026-09-28T10:00:00Z",
                                "latitude": 55.9,
                                "longitude": -3.2,
                                "horizontalAccuracy": 8,
                            }
                        ]
                    }
                ).encode("utf-8")
            ),
        )
    return path


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.out = self.folder / "out"

    def tearDown(self):
        self.temp.cleanup()

    def test_convert_command(self):
        archive = write_archive(self.folder)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["convert", "--input", str(archive), "--out", str(self.out), "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("wrote 1 files, 1 points", stdout.getvalue())
        self.assertTrue((self.out / "manifest.json").exists())

    def test_convert_command_accepts_the_zip_alias(self):
        archive = write_archive(self.folder)
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["convert", "--zip", str(archive), "--out", str(self.out), "--quiet"])
        self.assertEqual(code, 0)

    def test_convert_command_reports_missing_input(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "convert",
                    "--input",
                    str(self.folder / "missing.zip"),
                    "--out",
                    str(self.out),
                    "--quiet",
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("input not found", stderr.getvalue())

    def test_import_command_requires_a_token(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["import", "--dir", str(self.folder), "--quiet"])
        self.assertEqual(code, 2)
        self.assertIn("REITTI_API_TOKEN", stderr.getvalue())

    def test_invalid_week_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as raised:
            main(
                [
                    "convert",
                    "--zip",
                    "export.zip",
                    "--out",
                    "out",
                    "--weeks",
                    "not-a-week",
                ]
            )
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
