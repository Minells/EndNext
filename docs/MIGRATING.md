# Migrating EndNext

Install the intended version in a fresh Python 3.12 or newer environment and
record the output of `endnext --version` for your CI logs. The [quick start](../README.md#quick-start)
uses the repository checkout. Before upgrading a project, retain its contract,
suite configuration, and original JSON evidence.

## Carry forward saved evidence

`report` and `diff` accept saved v1 and v2 evidence. V1 records that carry a
single assertion can establish a comparison for that assertion. V2 reports
include all observed assertions and their counts. Keep the case and profile
names stable while their identities and expectations are the same. For
automation, use the documented exit codes and structured output, including
`diff --format json`. See the [compatibility contract](../README.md#compatibility).

```sh
endnext report baseline.json --format markdown
endnext diff baseline.json current.json --format json
```

Save both evidence files and the comparison result when archiving a CI check.
If a baseline assertion could not be verified in the current report, read the
`unverified` entries before replacing the baseline. Use [Python API v1](PYTHON_API.md)
when checking several local fixtures against the same contract from Python.

## Platform verification

| Platform | Checks in CI |
| --- | --- |
| Linux and macOS | Full test suite with the 96% coverage floor, plus wheel and source distribution installation and CLI checks |
| Windows | Installation from a wheel with Python 3.12 in UTF-8 mode, followed by offline contract, planning, reporting, comparison, simulated resilience, and Python API tests |

Live `scan` and `resilience-proxy` require a POSIX process for their hard
timeouts. Use the repository's synthetic fixtures on Windows to verify the
offline workflow.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\endnext.exe inspect examples\items.openapi.json
.\.venv\Scripts\endnext.exe check examples\items.openapi.json examples\item-pass.json
```

## Release compatibility and disclosure

Starting with stable releases, use SemVer for the documented CLI and Python
interfaces. An incompatible change requires a major release and migration
instructions. A compatible feature increments the minor version, and a
compatible fix increments the patch version. The evidence schema and Python
API module retain their own versions. Record user-facing changes in the
[changelog](../CHANGELOG.md) and update this guide when migration steps change.

Report suspected vulnerabilities through the private GitHub reporting link
in the [security policy](../SECURITY.md#report-a-vulnerability-privately). The
maintainer coordinates triage, a fix, and disclosure through that process.
