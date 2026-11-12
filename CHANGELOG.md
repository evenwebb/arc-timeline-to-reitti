# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.1] - 2026-11-12

### Fixed

- Authentication and other `4xx` failures are no longer retried. Only network errors, `429` and `5xx` responses are retried.

## [1.0.0] - 2026-11-12

### Added

- Convert Arc Timeline 4 weekly `.json.gz` exports into Reitti-compatible GeoJSON.
- One point per timestamp, keeping the best horizontal accuracy.
- UTC timestamp normalisation, elevation support and a 50 m accuracy fallback.
- Per-week and total reporting in `manifest.json`.
- Resumable upload to Reitti's GeoJSON import API with retries and an import log.
- Command line interface, test suite and GitHub Actions CI.
