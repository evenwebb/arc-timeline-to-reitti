from __future__ import annotations

import contextlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from arc_to_reitti.importer import ReittiImportError, _multipart, import_directory, read_log


def write_geojson(folder: Path, name: str, features: list[dict]) -> Path:
    path = folder / name
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return path


@contextlib.contextmanager
def reitti_server(statuses: list[int] | None = None):
    statuses = statuses or [200]
    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            calls.append({"path": self.path, "headers": self.headers, "body": body})
            status = statuses[min(len(calls) - 1, len(statuses) - 1)]
            payload = (
                b'{"success":true,"message":"imported"}'
                if status == 200
                else b'{"success":false,"message":"bad token"}'
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.log = self.folder / "import.log"

    def tearDown(self):
        self.temp.cleanup()

    def test_uploads_non_empty_files_and_skips_empty_ones(self):
        write_geojson(self.folder, "2026-W40.geojson", [{"type": "Feature"}])
        write_geojson(self.folder, "2026-W41.geojson", [])
        with reitti_server() as (url, calls):
            result = import_directory(
                self.folder, url, "secret-token", log_path=self.log, retries=0
            )

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["empty"], 1)
        self.assertEqual(result["already_imported"], 0)
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request["path"], "/api/v1/geojson/import")
        self.assertEqual(request["headers"].get("X-API-Token"), "secret-token")
        self.assertIn(b'name="device"', request["body"])
        self.assertIn(b"\r\n\r\n1\r\n", request["body"])
        self.assertIn(b'filename="2026-W40.geojson"', request["body"])

        log = self.log.read_text()
        self.assertIn("2026-W40.geojson", log)
        self.assertIn('"skipped":"empty"', log)

        with reitti_server() as (url, calls):
            again = import_directory(
                self.folder, url, "secret-token", log_path=self.log, retries=0
            )
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["already_imported"], 2)
        self.assertEqual(again["empty"], 0)
        self.assertEqual(calls, [])

    def test_rejected_upload_is_logged_and_raised(self):
        write_geojson(self.folder, "2026-W40.geojson", [{"type": "Feature"}])
        with reitti_server(statuses=[401]) as (url, _):
            with self.assertRaises(ReittiImportError):
                import_directory(self.folder, url, "bad-token", log_path=self.log, retries=0)

        log = self.log.read_text()
        self.assertIn('"success":false', log)
        self.assertIn("HTTP 401", log)

    def test_does_not_retry_an_authentication_failure(self):
        write_geojson(self.folder, "2026-W40.geojson", [{"type": "Feature"}])
        with reitti_server(statuses=[401]) as (url, calls):
            with mock.patch("arc_to_reitti.importer.time.sleep") as sleep:
                with self.assertRaises(ReittiImportError):
                    import_directory(self.folder, url, "bad-token", log_path=self.log, retries=3)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_retries_a_transient_failure(self):
        write_geojson(self.folder, "2026-W40.geojson", [{"type": "Feature"}])
        with reitti_server(statuses=[500, 200]) as (url, calls):
            with mock.patch("arc_to_reitti.importer.time.sleep") as sleep:
                result = import_directory(
                    self.folder, url, "secret-token", log_path=self.log, retries=1
                )
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(1)

    def test_multipart_escapes_unsafe_filename_characters(self):
        path = self.folder / 'weird".geojson'
        path.write_text("{}", encoding="utf-8")
        body, content_type = _multipart(1, path)
        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b'filename="weird_.geojson"', body)
        self.assertNotIn(b'filename="weird".geojson"', body)

    def test_token_is_required(self):
        write_geojson(self.folder, "2026-W40.geojson", [{"type": "Feature"}])
        with self.assertRaises(ReittiImportError):
            import_directory(self.folder, "http://127.0.0.1:1", "", log_path=self.log)

    def test_missing_directory_and_empty_directory_are_reported(self):
        with self.assertRaises(ReittiImportError):
            import_directory(self.folder / "missing", "http://127.0.0.1:1", "token")
        with self.assertRaises(ReittiImportError):
            import_directory(self.folder, "http://127.0.0.1:1", "token")

    def test_log_reader_ignores_failures_and_garbage(self):
        self.log.write_text(
            "a.geojson {\"success\":true}\n"
            "b.geojson {\"success\":false,\"error\":\"nope\"}\n"
            "garbage\n"
            "c.geojson not-json\n"
            "d.geojson {\"success\":true,\"skipped\":\"empty\"}\n",
            encoding="utf-8",
        )
        self.assertEqual(read_log(self.log), {"a.geojson", "d.geojson"})
        self.assertEqual(read_log(self.folder / "missing.log"), set())


if __name__ == "__main__":
    unittest.main()
