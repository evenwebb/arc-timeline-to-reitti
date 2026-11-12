<div align="center">

# Arc Timeline → Reitti

**Turn your Arc Timeline 4 history into a clean, importable Reitti timeline.**

[Quick start](#quick-start) · [Commands](#commands) · [Tested](#tested) · [Limitations](#limitations)

![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)
![Reitti](https://img.shields.io/badge/Reitti-5.10.1-2ea44f)
![Arc Timeline](https://img.shields.io/badge/Arc%20Timeline-schema%202.2.0-6f42c1)
![Licence](https://img.shields.io/badge/licence-GPL--3.0-blue)

</div>

---

Arc Timeline stores a weekly `.json.gz` file inside its export zip. Reitti's GeoJSON importer reads point geometry, a timestamp, accuracy and elevation. This tool pulls those samples out, removes duplicates, normalises timestamps and writes one clean `.geojson` file per week. A second command uploads the result to Reitti and keeps a log so the import can be resumed.

The converter is pure Python with no third-party dependencies.

> **Provided as is.** This is a finished tool, not an actively maintained project. It is tested against Arc Timeline 4 export schema 2.2.0 and Reitti 5.10.1. If either program changes its export format or import API, this tool may need changes.

## At a glance

| | |
| --- | --- |
| **Input** | Arc Timeline 4 weekly files, in a zip, folder, nested archive or mix |
| **Output** | One `.geojson` file per week, plus `manifest.json` |
| **Target** | Reitti 5.10.1 GeoJSON import API |
| **Runtime** | Python 3.10 or newer, no third-party packages |
| **Licence** | GNU General Public License v3.0 only |

## Requirements

- Python 3.10 or newer.
- Arc Timeline 4 weekly exports in any of the layouts below.
- A running Reitti instance with the GeoJSON import API enabled.
- A Reitti API token for the device that should receive the data.

## Accepted input layouts

`--input` accepts a file or a directory. It looks through nested folders and zip archives automatically.

| Layout | Example |
| --- | --- |
| One zip of weekly files | `Archive copy.zip` containing `2026-W40.json.gz` |
| One weekly file | `2026-W40.json.gz` or `2026-W40.json` |
| A folder of weekly files | `exports/` containing many `.json.gz` files |
| A folder of zip archives | `exports/` containing several `.zip` files |
| A zip of zip archives | `all-exports.zip` containing other `.zip` files |
| Nested folders | `exports/2026/2026-W40.json.gz` |
| Mixed layouts | A folder containing weekly files and zip archives |

If the same week appears in more than one container, the samples are combined and deduplicated by timestamp. That makes it safe to point at a full export plus an incremental export, or at both a zip and its extracted files.

## Install

From GitHub with `pipx`:

```sh
pipx install "git+https://github.com/evenwebb/arc-timeline-to-reitti.git"
```

Or with pip:

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
arc-to-reitti convert --input "Archive copy.zip" --out ./arc-geojson
```

The same command works for a single weekly file, a folder of weekly files, a folder of zips or a zip of zips:

```sh
arc-to-reitti convert --input ./exports --out ./arc-geojson
arc-to-reitti convert --input 2026-W40.json.gz --out ./arc-geojson
```

You will see one line per week:

```text
[1/487] 2017-W29: 1422 points (33 duplicate timestamps, 0 skipped)
...
wrote 487 files, 3439593 points to arc-geojson
```

Only some weeks:

```sh
arc-to-reitti convert --input "Archive copy.zip" --out ./arc-geojson --weeks 2026-W40,2026-W41
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
| --- | :---: | --- |
| `--input PATH` | yes | Arc export zip, weekly `.json.gz`/`.json` file, or directory. `--zip` is accepted as an alias. |
| `--out PATH` | yes | Directory for the generated `.geojson` files and `manifest.json`. Created if missing. |
| `--weeks LIST` | no | Comma-separated ISO weeks, for example `2026-W40,2026-W41`. Default: every week in the archive. |
| `--quiet` | no | Only print the final summary. |

Exit codes: `0` success, `1` conversion error.

### `arc-to-reitti import`

| Option | Required | Description |
| --- | :---: | --- |
| `--dir PATH` | yes | Directory produced by `convert`. |
| `--url URL` | no | Reitti base URL. Default: `http://127.0.0.1:8088/reitti`. |
| `--token TOKEN` | no | Reitti API token. Prefer `REITTI_API_TOKEN` so the token is not in your shell history or process list. |
| `--device ID` | no | Reitti device id. Default: `1`. |
| `--log PATH` | no | Import log. Default: `DIR/import.log`. |
| `--retries N` | no | Retries per file after a network error, `429` or `5xx` response. Default: `3`, with 1 s, 2 s and 4 s waits. Authentication and other `4xx` errors stop immediately. |
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
    "sources": 487,
    "samples": 3468182,
    "points": 3439593,
    "duplicate_timestamps": 21798,
    "skipped": 6791
  },
  "weeks": [
    {
      "period": "2017-W29",
      "file": "2017-W29.geojson",
      "sources": ["Archive copy.zip:2017-W29.json.gz"],
      "samples": 1422,
      "points": 1422,
      "duplicate_timestamps": 33,
      "skipped": 0,
      "first": "2017-07-17T08:00:00Z",
      "last": "2017-07-23T21:59:59Z"
    }
  ]
}
```

## How samples are handled

| Rule | Behaviour |
| --- | --- |
| **One point per timestamp** | When two samples share the same instant, the sample with the better horizontal accuracy wins. Every duplicate is counted. |
| **UTC timestamps** | Offsets are converted and written with a trailing `Z`. Samples are sorted by parsed time. |
| **Accuracy fallback** | A missing or invalid accuracy becomes `50 m`, so Reitti's accuracy-aware processing still works. |
| **Elevation** | Written as a third coordinate and an `elevation` property when Arc recorded a finite altitude. |
| **Bad samples** | Disabled samples, missing or non-numeric coordinates, out-of-range coordinates and unparseable dates are skipped and counted. |
| **Empty weeks** | Written as an empty `FeatureCollection`; the import command logs and skips them. |

Arc visit names, place categories and trip activity types are **not** copied. Reitti's GeoJSON importer ignores them; Reitti derives visits, places and trips from the points.

## Import behaviour

- Files are uploaded in filename order, so weeks go in chronologically.
- Reitti holds an upload for a short grace period before promoting the points and running visit and trip detection. This is Reitti's behaviour, not the converter's.
- Successful uploads are recorded in `import.log`. A rerun skips them.
- Failed uploads are recorded with `"success":false` and the command stops with exit code `1`. Fix the cause and rerun; successful files are skipped.
- Empty weeks are recorded as `{"success":true,"skipped":"empty"}` and are never uploaded.
- If you delete `import.log`, the next run uploads every file again. Keep the log for a resumable import.

## Tested

Tested on **2026-11-12** with:

| Component | Version |
| --- | --- |
| Arc Timeline 4 | export schema **2.2.0** |
| Reitti | **5.10.1** |
| Python | **3.10, 3.11, 3.12 and 3.13** |

### Full history

The full Arc export was converted and imported end to end:

| Measure | Result |
| --- | ---: |
| Weeks converted | 487 |
| Date range | 2017-W29 to 2026-W46 |
| Samples in export | 3,468,182 |
| Points written | 3,439,593 |
| Duplicate timestamps collapsed | 21,798 |
| Samples skipped | 6,791 |
| Empty weeks | 224 |
| Full conversion time | 112 seconds |
| Reitti source points | 3,439,593 |
| Reitti raw points after gap filling | 8,701,494 |
| Reitti significant places | 2,016 |
| Reitti visits | 8,652 |
| Reitti trips | 8,602 |
| Reitti transport-mode segments | 11,326 |

The totals and spot-checked weeks matched the earlier reference import exactly. A test week, `2026-W40` with 20,358 points, was opened in the Reitti UI and checked after import.

### Container layouts

The real archive was also converted through each supported layout, with matching totals:

| Layout | Result |
| --- | --- |
| Zip of weekly files | 2 weeks, 21,780 points |
| Single weekly `.json.gz` | 1 week, 20,358 points |
| Folder of weekly files | 2 weeks, 21,780 points |
| Zip of zip archives | 2 weeks, 21,780 points |
| Mixed containers with a duplicate week | Samples combined, one point per timestamp |

### Automated checks

- **35 tests** covering conversion, duplicate handling, timestamp ordering, archive validation, every input layout, the CLI and the Reitti upload path.
- **GitHub Actions** runs the suite on Python 3.10, 3.11, 3.12 and 3.13; all four jobs pass.
- A wheel was built and installed into a clean virtual environment, and the installed `arc-to-reitti` command reported the expected version.

The uploader is tested against a local HTTP server rather than a live Reitti instance, so a first import into a fresh Reitti instance is still worth watching.

## Scale and disk use

The converter holds one week in memory at a time, so memory use stays bounded by the largest weekly file. Output is roughly **200 MB per million points**. For a multi-year history, make sure the output directory has room; the original Arc zip can stay where it is.

Nested zip archives are extracted to a temporary folder while converting and removed afterwards. Leave room in the system temp directory for the largest nested archive.

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

## Limitations

- Arc Timeline 4 only. Other export formats are not supported.
- Only point samples are converted. Visits, trips, place names and activity types come from Reitti's own processing.
- The importer talks to Reitti's GeoJSON API. A Reitti version that changes that endpoint or its multipart field names will need an update here.
- Re-importing without `import.log` can create duplicate history in Reitti.
- There is no built-in rate limit. The importer is sequential, which is gentle on a single Reitti instance.
- Nested zip archives are followed up to five levels deep.
- Future versions of Arc Timeline or Reitti are not covered by the testing above.

## Licence

GNU General Public License v3.0 only. See [LICENSE](LICENSE).
