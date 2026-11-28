# Changelog

## Unreleased

### Added

- Run a TOML plan of saved responses with `endnext suite`, producing one evidence report for comparison in CI.

- Added the versioned offline Python API `endnext.api_v1` for checking multiple saved responses against one validated contract. [PR #17](https://github.com/Minells/EndNext/pull/17)
- Added a repeatable benchmark for rule verdicts, inventory size, and contract reuse. [PR #18](https://github.com/Minells/EndNext/pull/18)
- Added a migration guide and a Windows CI check for the documented offline workflows. See [Migrating EndNext](docs/MIGRATING.md).

### Changed

- Documented the CLI compatibility contract and added regression coverage for v1 and v2 reports and stable fingerprints. [PR #15](https://github.com/Minells/EndNext/pull/15)
- Shared the loopback request policy across planning and dispatch. The transport checks the scope and accepts bodyless GET requests. [PR #16](https://github.com/Minells/EndNext/pull/16)

### Fixed

- The resilience proxy emits its interrupted summary when launched with `SIGINT` ignored. [PR #16](https://github.com/Minells/EndNext/pull/16)
- Evaluate explicit fixture access expectations, validate structured JSON media types, and recognize bodyless HTTP responses.
