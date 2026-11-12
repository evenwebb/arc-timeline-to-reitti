# Arc Timeline to Reitti

Convert an **Arc Timeline 4** export into GeoJSON that **Reitti** can import, then upload it to Reitti in one command.

Arc Timeline stores a weekly `.json.gz` file inside its export zip. Reitti's GeoJSON importer only reads point geometry, a timestamp, accuracy and elevation. This tool extracts those samples, removes duplicates, normalises timestamps and writes one clean `.geojson` file per week.

The converter is pure Python with no third-party dependencies. It has been used to import a 487-week Arc history (3.4 million points) into Reitti.

## Requirements

- Python 3.10 or newer.
- An Arc Timeline 4 export zip containing weekly `.json.gz` files.
- A running Reitti instance with the GeoJSON import API enabled.
- A Reitti API token for the device that should receive the data.

## Install

From the private repository with `pipx`:

```sh
pipx install "git+https://github.com/evenwebb/arc-timeline-to-reitti.git"
```

Or install with pip:

```sh
python3 -m pip install --user "git+https://github.com/evenwebb/arc-timeline-to-reitti.git"
```

From a clone:

```sh
git clone https://github.com/evenwebb/arc-timeline-to-reitti.git
cd arc-timeline-to-reitti
python3 -m pip install .
```

This installs the `arc-to-reitti` command. You can also run it without installing:

```sh
python3 -m arc_to_reitti --help
```

## Quick start

### 1. Export from Arc Timeline 4

In Arc Timeline, export your timeline as the weekly JSON archive. The export is a zip containing one `.json.gz` file per ISO week, for example `2026-W40.json.gz`.

The zip may contain a top-level folder. This tool handles that.

### 2. Convert

```sh
arc-to-reitti convert --zip "Archive copy.zip" --out ./arc-geojson
```

You will see one line per week:

```text
[1/487] 2017-W29: 1204 points (0 duplicate timestamps, 3 skipped)
...
wrote 487 files, 3439593 points to arc-geojson
```

Only some weeks:

```sh
arc-to-reitti convert --zip "Archive copy.zip" --out ./arc-geojson --weeks 2026-W40,2026-W41
```

### 3. Import into Reitti

Create an API token in Reitti for the target device, then run:

```sh
export REITTI_API_TOKEN="your-token"
arc-to-reitti import \
  --dir ./arc-geojson \
  --url "http://127.0.0.1:8088/reitti" \
  --device 1
```

`--url` is the base URL of your Reitti instance, including any context path. The tool appends `/api/v1/geojson/import` itself. If Reitti is served at `https://example.com/reitti`, pass `--url "https://example.com/reitti"`.

The import is resumable. Each successful upload is written to `arc-geojson/import.log`, and the next run skips those files. Empty weeks are logged and skipped automatically.

## Commands

### `arc-to-reitti convert`

| Option | Required | Description |
| --- | --- | --- |
| `--zip PATH` | yes | Arc Timeline export zip. |
| `--out PATH` | yes | Directory for the generated `.geojson` files and `manifest.json`. Created if missing. |
| `--weeks LIST` | no | Comma-separated ISO weeks, for example `2026-W40,2026-W41`. Default: every week in the archive. |
| `--quiet` | no | Only print the final summary. |

Exit codes: `0` success, `1` conversion error.

### `arc-to-reitti import`

| Option | Required | Description |
| --- | --- | --- |
| `--dir PATH` | yes | Directory produced by `convert`. |
| `--url URL` | no | Reitti base URL. Default: `http://127.0.0.1:8088/reitti`. |
| `--token TOKEN` | no | Reitti API token. Prefer the `REITTI_API_TOKEN` environment variable so the token is not in your shell history or process list. |
| `--device ID` | no | Reitti device id. Default: `1`. |
| `--log PATH` | no | Import log. Default: `DIR/import.log`. |
| `--retries N` | no | Retries per file after a failed request. Default: `3`, with 1 s, 2 s and 4 s waits. |
| `--timeout SECONDS` | no | HTTP timeout per upload. Default: `120`. |
| `--quiet` | no | Only print the final summary. |

Exit codes: `0` success, `1` conversion or upload error, `2` missing token or invalid arguments.

## Output

```text
arc-geojson/
├── 2017-W29.geojson
├── 2017-W30.geojson
├── ...
├── manifest.json
└── import.log        # created by the import command
```

Each `.geojson` file is a standard GeoJSON `FeatureCollection` with one Point feature per unique timestamp:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [-3.2, 55.9, 55.0]
  },
  "properties": {
    "timestamp": "2026-09-28T10:00:00Z",
    "accuracy": 10.0,
    "elevation": 55.0
  }
}
```

`manifest.json` records the source file name, totals and a per-week report:

```json
{
  "generated": "2026-11-12T14:00:00+00:00",
  "source": "Archive copy.zip",
  "totals": {
    "weeks": 487,
    "samples": 3468182,
    "points": 3439593,
    "duplicate_timestamps": 21798,
    "skipped": 6791
  },
  "weeks": [
    {
      "period": "2017-W29",
      "file": "2017-W29.geojson",
      "samples": 1207,
      "points": 1204,
      "duplicate_timestamps": 0,
      "skipped": 3,
      "first": "2017-07-17T08:00:00Z",
      "last": "2017-07-23T21:59:59Z"
    }
  ]
}
```

## How samples are handled

- **One point per timestamp.** When two samples share the same instant, the sample with the better horizontal accuracy wins. Every duplicate is counted in the manifest.
- **Timestamps are normalised to UTC.** Offsets are converted and written with a trailing `Z`. Samples are sorted by time.
- **Missing accuracy becomes 50 m.** This keeps Reitti's accuracy-aware processing usable when Arc did not record a value.
- **Elevation is optional.** It is written as a third coordinate and as an `elevation` property when Arc recorded a finite altitude.
- **Bad samples are skipped and counted.** Disabled samples, missing or non-numeric coordinates, coordinates outside valid ranges, and unparseable dates are not written.
- **Empty weeks are valid.** A week with no usable samples becomes an empty `FeatureCollection`. The import command logs it and moves on.

Arc visit names, place categories and trip activity types are **not** copied. Reitti's GeoJSON importer ignores them; Reitti derives visits, places and trips from the points.

## Import behaviour

- Files are uploaded in filename order, so weeks go in chronologically.
- Reitti holds an upload for a short grace period before promoting the points and running visit and trip detection. This is Reitti's behaviour, not the converter's.
- Successful uploads are recorded in `import.log`. A rerun skips them.
- Failed uploads are recorded with `"success":false` and the command stops with exit code `1`. Fix the cause and rerun; successful files are skipped.
- Empty weeks are recorded as `{"success":true,"skipped":"empty"}` and are never uploaded.
- If you delete `import.log`, the next run uploads every file again. Keep the log for a resumable import.

## Scale and disk use

The converter holds one week in memory at a time, so memory use stays bounded by the largest weekly file. Output is roughly 200 MB per million points. For a multi-year history, make sure the output directory has room; the original Arc zip can stay where it is.

The import command uploads one file at a time. A large history takes a while because Reitti processes each upload asynchronously. You can stop the import and rerun it later; the log makes it safe to resume.

## Security and privacy

- The converter runs locally and sends nothing anywhere.
- The import command sends data only to the Reitti URL you provide.
- The API token is read from `REITTI_API_TOKEN` by default. `--token` exists for convenience but is visible in the process list and shell history.
- `import.log` contains filenames and Reitti responses, not the token. It is excluded by the default `.gitignore`.
- No telemetry.

## Development

```sh
git clone https://github.com/evenwebb/arc-timeline-to-reitti.git
cd arc-timeline-to-reitti
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

The test suite covers conversion, duplicate handling, timestamp normalisation, archive validation, the CLI and the Reitti upload path against a local HTTP server. GitHub Actions runs the same tests on Python 3.10, 3.11, 3.12 and 3.13.

## Limitations

- Arc Timeline 4 only. Other export formats are not supported.
- Only point samples are converted. Visits, trips, place names and activity types come from Reitti's own processing.
- The importer talks to Reitti's GeoJSON API. A Reitti version that changes that endpoint or its multipart field names will need an update here.
- Re-importing without `import.log` can create duplicate history in Reitti.
- There is no built-in rate limit. The importer is sequential, which is gentle on a single Reitti instance.

## License

MIT. See [LICENSE](LICENSE).
