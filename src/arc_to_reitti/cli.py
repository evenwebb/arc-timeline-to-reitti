"""Command line interface for the Arc Timeline to Reitti converter."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .convert import PERIOD_RE, ConversionError, convert_archive
from .importer import ReittiImportError, import_directory


def _weeks(value: str) -> list[str]:
    weeks = [item.strip() for item in value.split(",") if item.strip()]
    invalid = [item for item in weeks if not PERIOD_RE.match(item)]
    if invalid:
        raise argparse.ArgumentTypeError(f"invalid week ids: {', '.join(invalid)}")
    return weeks


def _convert_progress(index: int, total: int, result: dict) -> None:
    print(
        f"[{index}/{total}] {result['period']}: {result['points']} points "
        f"({result['duplicate_timestamps']} duplicate timestamps, {result['skipped']} skipped)",
        flush=True,
    )


def _import_progress(index: int, total: int, path: Path, status: str) -> None:
    print(f"[{index}/{total}] {path.name}: {status}", flush=True)


def _add_convert_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "convert",
        help="convert an Arc Timeline zip archive to GeoJSON",
        description="Convert Arc Timeline weekly JSON exports into Reitti-compatible GeoJSON.",
    )
    parser.add_argument(
        "--input",
        "--zip",
        dest="input",
        required=True,
        type=Path,
        help="Arc export zip, weekly .json.gz/.json file, or directory containing either",
    )
    parser.add_argument("--out", required=True, type=Path, help="directory for .geojson files")
    parser.add_argument(
        "--weeks",
        type=_weeks,
        help="comma-separated weeks to convert, for example 2026-W40,2026-W41",
    )
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    parser.set_defaults(handler=_run_convert)


def _add_import_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "import",
        help="upload converted files to Reitti",
        description="Upload converted GeoJSON files to a Reitti instance.",
    )
    parser.add_argument("--dir", required=True, type=Path, help="directory of .geojson files")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8088/reitti",
        help="Reitti base URL, including any context path (default: %(default)s)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Reitti API token; prefer the REITTI_API_TOKEN environment variable",
    )
    parser.add_argument("--device", type=int, default=1, help="Reitti device id (default: %(default)s)")
    parser.add_argument("--log", type=Path, default=None, help="import log (default: DIR/import.log)")
    parser.add_argument("--retries", type=int, default=3, help="retries per file (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=120.0, help="request timeout in seconds")
    parser.add_argument("--quiet", action="store_true", help="only print the final summary")
    parser.set_defaults(handler=_run_import)


def _run_convert(args: argparse.Namespace) -> int:
    try:
        result = convert_archive(
            args.input,
            args.out,
            weeks=args.weeks,
            progress=None if args.quiet else _convert_progress,
        )
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    totals = result["manifest"]["totals"]
    print(
        f"wrote {totals['weeks']} files, {totals['points']} points to {args.out}",
        flush=True,
    )
    return 0


def _run_import(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("REITTI_API_TOKEN", "")
    if not token:
        print(
            "error: set REITTI_API_TOKEN or pass --token",
            file=sys.stderr,
        )
        return 2
    try:
        result = import_directory(
            args.dir,
            args.url,
            token,
            device=args.device,
            log_path=args.log,
            retries=args.retries,
            timeout=args.timeout,
            progress=None if args.quiet else _import_progress,
        )
    except ReittiImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"imported {result['imported']}, already imported {result['already_imported']}, "
        f"empty {result['empty']}; log: {result['log']}",
        flush=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arc-to-reitti",
        description="Convert Arc Timeline 4 exports into Reitti-compatible GeoJSON.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_convert_parser(subparsers)
    _add_import_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
