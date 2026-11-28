<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/branding/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/branding/logo-light.svg">
    <img src="assets/branding/logo.svg" width="460" alt="EndNext">
  </picture>
</h1>

<p align="center"><strong>Open-source API verification for OpenAPI 3.1.</strong></p>

<p align="center">
  Validate captured JSON responses, run scoped checks against local services,<br>
  and compare evidence across runs to catch regressions.
</p>

<p align="center">
  <a href="#quick-start"><img src="assets/branding/badges/python.svg" alt="Python 3.12 or later"></a>
  <a href="https://github.com/Minells/EndNext/actions/workflows/main-ci.yml"><img src="https://github.com/Minells/EndNext/actions/workflows/main-ci.yml/badge.svg?branch=main" alt="Main CI status"></a>
  <a href="LICENSE"><img src="assets/branding/badges/license.svg" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#quick-start"><strong>Quick start</strong></a> ·
  <a href="#features">Features</a> ·
  <a href="#local-http-checks">Local HTTP checks</a> ·
  <a href="#compare-runs">Compare runs</a> ·
  <a href="#documentation">Docs</a> ·
  <a href="https://github.com/Minells/EndNext/issues">Issues</a>
</p>

> **Pre-release.** Install EndNext from this repository. The examples use synthetic data and run offline or against a bundled loopback service.

## Why EndNext

When an API response breaks its contract, you need to know which operation and field changed and whether the failure is new. EndNext turns a saved response into evidence you can review even after the service shuts down. For local integration testing, it can also collect responses from explicitly selected endpoints.

## How it works

```text
OpenAPI 3.1 contract + saved response         → check → JSON evidence
OpenAPI 3.1 contract + TOML suite              → plan  → cases and request budget
OpenAPI 3.1 contract + authorized local suite → scan  → JSON evidence
TOML dependency scenario → resilience → simulated HTTP evidence

Saved evidence              → report → terminal or Markdown
Baseline + current evidence → diff   → regression summary
```

## Quick start

From the repository root on macOS or Linux, create a virtual environment with Python 3.12 or later and install the CLI.

```sh
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Inspect the sample contract, then validate a passing response and a response with a schema mismatch.

```sh
endnext inspect examples/items.openapi.json
endnext check examples/items.openapi.json examples/item-pass.json
endnext check examples/items.openapi.json examples/item-fail.json
```

The final command exits with code `1` and identifies the failing field.

```text
FAIL GET /items/{id} (getItem) · response.body.schema at /quantity
The schema expects integer. The observed JSON type is string. Observation: imported fixture, HTTP 200.
```

<details>
<summary>Validate your own captured response</summary>

1. Run `endnext inspect contract.yaml` to find the method, exact path template, media type, and security declarations.
2. Copy [the passing fixture](examples/item-pass.json) and set `case`, `method`, and `path_template`. Fill in the scalar values in `path_parameters` when the template has parameters.
3. Set `status` to the observed HTTP status and `media_type` to `application/json`. Set `body` to the captured JSON value and run `endnext check contract.yaml response.json`.

Contracts can be local JSON or JSON-compatible YAML files up to 1 MiB, with references within the same document. Quote numeric YAML keys such as `'200'`. Each invocation of `check` validates one captured response. Reports retain operation, status, provenance, and schema mismatch details while omitting response bodies, parameter values, and raw validator messages. Use shareable case and file names. [Validator notes](docs/VALIDATOR.md)

</details>

## Features

| Command | What it does |
| --- | --- |
| `inspect` | Lists operations, response media types, and contract-level and operation-level security declarations. |
| `check` | Validates a captured response against documented status, media type, and schema. Fixtures can also carry expected status, anonymous access, and cache policy assertions. |
| `plan` | Previews selected operations, effective security, skipped cases, candidate assertions, and estimated request cost entirely offline. |
| `scan` | Runs explicitly configured GET cases against an authorized loopback service, including access checks with named bearer profiles. |
| `resilience` | Replays simulated dependency outcomes and checks attempts, final status, and recovery time. |
| `resilience-generate` | Uses a seed to turn a template's alternatives into a concrete TOML scenario. |
| `resilience-proxy` | Applies a fault sequence to HTTP/1.1 requests and forwards selected steps to an authorized loopback dependency. |
| `resilience-check` | Checks a saved proxy trace and real client measurement against a resilience scenario. |
| `correlate` | Joins scan findings with a completed resilience assessment from the same measured request. |
| `report` | Renders saved v1 and v2 JSON evidence as terminal output or Markdown while preserving verdicts, provenance, and counts. |
| `diff` | Compares two evidence files and identifies new failures, resolved failures, and checks that could not be verified. |

Use `--format json` with `inspect`, `check`, `plan`, `scan`, `resilience`, `resilience-check`, `correlate`, or `diff` to get structured output on stdout.

### Preview a suite offline

```sh
endnext plan examples/items.openapi.json examples/items-plan.toml
```

The sample plan shows one imported fixture, one HTTP case, and one skipped case with a missing fixture. It estimates one request and lists the assertions whose prerequisites must be met during execution.

<details>
<summary>How to define a suite</summary>

Use `select` to choose operations by method and exact path template. Give each `[[cases]]` entry a unique name, an operation, and a source. For `source = "fixture"`, resolve the fixture path relative to the TOML file. For `source = "http"`, specify a GET case with an expected status or access outcome and any scalar path and query parameters. The request budget defaults to zero and accepts values from 0 to 20. The same inputs produce the same plan without network access.

</details>

## Local HTTP checks

On POSIX, `scan` executes the GET cases selected in a TOML suite against a loopback service you are authorized to test. The target requires explicit authorization, a literal loopback IP address, a port, and a path prefix. Use `expected_status` for contract cases. For access cases, choose a named `auth_profile` and set `expected_access` to `"allow"` or `"deny"`. The profile reads its bearer token from the environment at runtime. Plans and reports identify the profile by name.

<details>
<summary>Run the bundled two-identity demo</summary>

The [demo suite](examples/authorization-plan.toml) already defines the loopback target, request budget, and profiles. Start the synthetic service in one terminal from the repository root with the virtual environment active.

```sh
export ENDNEXT_DEMO_ALICE_TOKEN=demo-alice-local
export ENDNEXT_DEMO_BOB_TOKEN=demo-bob-local
python examples/authorization_demo.py
```

In a second terminal, activate the same virtual environment and run the suite.

```sh
export ENDNEXT_DEMO_ALICE_TOKEN=demo-alice-local
export ENDNEXT_DEMO_BOB_TOKEN=demo-bob-local
endnext plan examples/authorization.openapi.json examples/authorization-plan.toml
endnext scan examples/authorization.openapi.json examples/authorization-plan.toml \
  --allow-bearer alice ENDNEXT_DEMO_ALICE_TOKEN http://127.0.0.1:8765 \
  --allow-bearer bob ENDNEXT_DEMO_BOB_TOKEN http://127.0.0.1:8765 --format json > current.json
endnext report current.json
```

| Case | Expected access | Observed status | Verdict |
| --- | --- | --- | --- |
| Alice reads her own document | Allow | 204 | `PASS` |
| Bob reads Alice's document | Deny | 403 | `PASS` |
| Bob reads his own document | Allow | 403 | `FAIL` |

The scan sends three requests and exits with code `1`. To generate a passing baseline, stop the demo server and restart it with `python examples/authorization_demo.py --healthy`. Run the scan again, writing to `baseline.json`. The healthy run returns HTTP 204 for Bob's own document and exits with code `0`. Run `endnext diff baseline.json current.json` to see the regression.

</details>

<details>
<summary>Read the transport limits and access verdicts</summary>

The transport allows one request per second, up to 20 attempts, with a five-second limit per request, a 60-second acquisition deadline, and up to 1 MiB of retained response body per response. It disables redirects, retries, environment proxies, and cookie propagation, and verifies HTTPS certificates. Reports distinguish request attempts from completed HTTP header sends.

With a target configured, `plan` checks the resolved path against the target prefix offline. Before sending, the transport verifies scope and allows only bodyless GET requests with the configured authority. A named bearer profile supplies its token at dispatch time.

The `security.authenticated_access` rule treats a 2xx response as allowed and a 403 as denied. A 401, ambiguous status, or partially read response yields `INCONCLUSIVE`. An unavailable credential or excluded case yields `SKIPPED`. Malformed tokens, timeouts, and transport failures yield `ERROR`. An access case with any of these three verdicts makes `scan` exit with code `2`. [Security policy](SECURITY.md)

</details>

## Reproduce a dependency retry

The [resilience scenario](examples/resilience-retry.toml) scripts HTTP 503, 429, then 200 for `GET /dependency`. It expects the client to reach HTTP 200 in exactly three attempts and within 250 ms of simulated latency.

```sh
endnext resilience examples/resilience-retry.toml --client retry --format json > baseline.json
endnext resilience examples/resilience-retry.toml --client single --format json > current.json
endnext report current.json
endnext diff baseline.json current.json
```

The `retry` client reaches HTTP 200 in three attempts and 200 simulated milliseconds. The `single` client stops on HTTP 503. Its final status and attempt count fail, and its recovery time is skipped. Both the `single` run and the comparison exit with code `1`. A simulation error or a run with no observed dependency attempts exits with code `2`.

Set `outcome` in each `[[steps]]` table to an HTTP status from 200 to 599, `"timeout"`, or `"disconnect"`. An optional `delay_ms` adds to the simulated clock. A scenario uses at most 20 steps, with up to 5000 ms per step and 60000 ms total. The demo's `retry` client allows up to three attempts and retries HTTP 429, HTTP 503, and both simulated transport errors. The ordered steps and input digest make a run replayable. The JSON report includes the outcome and accumulated simulated latency for each attempt.

For a sequential Python client under test, load `endnext.resilience.Scenario` and pass a callable to `endnext.resilience.run_scenario`. The callable receives an in-process HTTPX client for `GET /dependency` and returns a final HTTP status or `None`. The adapter opens no sockets. The timing assertion measures scripted dependency latency. [HTTPX mock transports](https://www.python-httpx.org/advanced/transports/#mock-transports)

### Generate and replay a seeded scenario

In a [scenario template](examples/resilience-template.toml), a step can list two to twenty `alternatives`. Each alternative is a step with `outcome` and an optional `delay_ms`. Fixed steps use their existing syntax. Generate the concrete file offline, then use that file when comparing two runs.

```sh
replay_dir=$(mktemp -d)
endnext resilience-generate examples/resilience-template.toml --seed 42 > "$replay_dir/scenario.toml"
endnext resilience "$replay_dir/scenario.toml" --client retry --format json > baseline.json
endnext resilience "$replay_dir/scenario.toml" --client single --format json > current.json
endnext diff baseline.json current.json
```

Seed `42` selects HTTP 503, 429, and 200 for this template. Seed `7` selects a disconnect, HTTP 503, and HTTP 200. The same template bytes and seed produce the same concrete file byte for byte. `generation` records the seed, algorithm, and template digest, and `scenario_digest` identifies the concrete file in the evidence. Keep that file or regenerate it from the recorded template and seed when reproducing a finding.

`resilience-proxy` and `resilience-check` also accept the concrete file. Use a fixed `outcome = "forward"` in the template when a step should contact the authorized loopback dependency. Pass `--scenario` to the live demo to use the generated file. The proxy `ready` event and resilience JSON reports carry the same generation metadata.

### Exercise a local dependency over HTTP/1.1

Start a synthetic dependency on loopback in one terminal.

```sh
demo_dir=$(mktemp -d)
printf 'healthy dependency\n' > "$demo_dir/dependency"
python -m http.server 8767 --bind 127.0.0.1 --directory "$demo_dir"
```

In a second terminal, start the proxy with the [forwarding scenario](examples/resilience-proxy.toml).

```sh
endnext resilience-proxy examples/resilience-proxy.toml \
  --upstream http://127.0.0.1:8767 --authorized
```

The `ready` event shows the listener origin. In a third terminal, run the following command three times.

```sh
curl -i http://127.0.0.1:8766/dependency
```

The client receives HTTP 503, HTTP 429, then the upstream HTTP 200 with `healthy dependency`. The proxy emits a JSON Lines event per step, recording the chosen response status and elapsed time since listener startup. Its final event shows three attempts and one upstream request. The proxy exits automatically at the end of the sequence. A replay cut short by an upstream error, a deadline, or Ctrl-C exits with code `2`.

The proxy accepts only HTTP/1.1 `GET /dependency` on `127.0.0.1`. The upstream must be a literal loopback origin with an explicit port. Port `0` chooses a free listener port. The proxy uses the scenario's ordered steps. A `forward` step fetches the fixed route from the upstream. Each `delay_ms` is applied in real time. A `disconnect` step closes the connection, and a `timeout` step holds it open for a positive `delay_ms` before closing. Set the test client's read timeout below that delay to observe a timeout.

One connection is handled at a time. The replay has a 60-second deadline and uses the scan transport's bounded upstream requests and response reads. Each upstream request is constructed by EndNext. Forwarded replies carry only the status, media type, and bounded body, including for redirect responses. Every record in the JSON Lines trace carries the proxy run ID.

### Compare real client recovery

With the synthetic dependency running on port 8767, capture separate HTTP/1.1 runs for the two demo clients. Each run uses its own listener and the same scenario file.

```sh
python examples/resilience_live_demo.py retry live-retry
python examples/resilience_live_demo.py single live-single
endnext resilience-check examples/resilience-proxy.toml live-retry/proxy.jsonl live-retry/client.json --format json > baseline.json
endnext resilience-check examples/resilience-proxy.toml live-single/proxy.jsonl live-single/client.json --format json > current.json
endnext report current.json
endnext diff baseline.json current.json
```

The retrying client reaches HTTP 200 in three attempts against a two-second recovery budget. The single-attempt client stops at HTTP 503. Its report has two failures and a skipped recovery-time check. The second `resilience-check` and `diff` exit with code `1`.

To use your own client, start `resilience-proxy` with `--observation client.json`. After the client finishes, atomically publish a JSON object with `run_id` from the `ready` event, `status` as the client's final HTTP status or `null`, and `elapsed_ms` as the client's total elapsed time in milliseconds. The proxy waits for the matching observation within the run deadline. It can finish early when the client stops before the sequence ends. A request beyond the scripted sequence before the observation is recorded ends the run with an execution error.

`resilience-check` loads both files offline. It verifies the run ID and the digests of the scenario and client measurement. It counts dependency attempts from the trace and uses the client measurement for final status and recovery time. An unconfirmed client completion, an interrupted run, or a run without observed attempts yields `INCONCLUSIVE`. A reported proxy execution error yields `ERROR`. Both exit with code `2`. Assessment requires a versioned proxy trace with run IDs.

### Link scan findings to a proxy run

Start the synthetic upstream on port 8767 as above. Start the proxy in a second terminal from the checkout, using a fresh directory for this run. Use the same paths when collecting evidence.

```sh
run_dir=$(mktemp -d)
printf '%s\n' "$run_dir"
endnext resilience-proxy examples/resilience-proxy.toml \
  --upstream http://127.0.0.1:8767 --authorized \
  --observation "$run_dir/client.json" > "$run_dir/proxy.jsonl"
```

In another terminal, run `python examples/resilience_api_demo.py`. The API listens on port 8768 and uses the proxy for its dependency. Once it is ready, run the following commands from the checkout and substitute the directory printed by the proxy terminal.

```sh
run_dir=/path/printed/by/proxy/terminal
endnext scan examples/items.openapi.json examples/resilience-scan.toml \
  --proxy-trace "$run_dir/proxy.jsonl" --proxy-observation "$run_dir/client.json" \
  --format json > "$run_dir/scan.json"
endnext resilience-check examples/resilience-proxy.toml "$run_dir/proxy.jsonl" \
  "$run_dir/client.json" --format json > "$run_dir/resilience.json"
endnext correlate "$run_dir/scan.json" "$run_dir/resilience.json"
```

Wait for the proxy to finish before running `resilience-check`. In this demo, the API returns HTTP 503 after one dependency attempt. Both check commands return `1` and write evidence. `correlate` joins the scan's status findings with the resilience assertions and displays the same scan fingerprints used by `diff`.

The two scan options require exactly one eligible live request. The scan measures the HTTP acquisition and publishes the observation atomically for the proxy to read. The offline join verifies the shared run ID, scenario digest, and measurement digest. It exits with `0` for a successful join and `2` when the files are missing, incompatible, or describe an incomplete resilience assessment.

## Reports and evidence

EndNext records individual assertions so a partial response can still show which checks had enough evidence. Use the policy fixture to see a passing schema check alongside an access mismatch and a cache warning.

```sh
endnext check examples/items.openapi.json examples/item-policy-fail.json --format json > evidence.json
endnext report evidence.json --format markdown
```

The fixture check deliberately exits with code `1` and writes valid JSON evidence. Use `endnext report evidence.json` for the terminal view.

<details>
<summary>Record partial responses and policy expectations</summary>

Set fixture `body_state` to `absent`, `truncated`, or `invalid` and omit `body` when the response lacks a complete JSON value. For HTTP 204, you can omit both body and media type. With `identity: "anonymous"`, the fixture can assert `expected_access` against an imported observation. With `sensitive: true`, `expected_cache_directive` checks for the named directive in `headers.Cache-Control`.

</details>

## Compare runs

Save JSON evidence from two runs of the same suite. In this example, `diff` flags the schema mismatch at `/quantity` as a new failure.

```sh
endnext check examples/items.openapi.json examples/item-pass.json --format json > baseline.json
endnext check examples/items.openapi.json examples/item-fail.json --format json > current.json
endnext diff baseline.json current.json
```

Keep case and profile names stable across runs. Rename a case when its resource or expectation changes, and rename a profile when its identity changes. EndNext matches checks by operation, case name, observation source, rule, and authentication profile where present. The failure fingerprint includes the field location.

Existing failures and warnings remain visible in the summary. A passing assertion confirms a resolution. If a check from the baseline is missing, skipped, or inconclusive in the current run, EndNext reports it as unverified.

| Exit code | `check` / `scan` | `diff` |
| --- | --- | --- |
| `0` | Completed without a failing or blocking incomplete check | Comparable evidence with no blocking failures or incomplete baseline checks |
| `1` | A failing assertion | A new failure without an active exception, or a current failure with an expired exception |
| `2` | Invalid input, execution error, or a blocking incomplete check | Invalid input, no comparable checks, or incomplete verification |

### Compatibility

For automation, use the documented command names, options, exit codes, and
structured output. Terminal and Markdown output are intended for people.
For commands that write JSON, a finding or an incomplete run can still produce
evidence. Check the exit code as well as the output before using it as a baseline.

`report` and `diff` read evidence reports with `schema_version` `"1"` or `"2"`.
You can compare a saved v1 baseline directly with v2 evidence. V1 records can
carry a single assertion, and the reader derives counts from the assertions
present. V2 records carry `assertions` arrays and aggregate `counts` that must
match those arrays. Both versions support an individual case or a `cases`
array. The JSON emitted by `diff` has its own schema (`baseline_diff`, version
`"1"`). Save the original evidence files for future comparisons.

The same finding has the same fingerprint across compatible report versions.
`diff` and `correlate` use `sha256:` followed by the lowercase SHA-256 hex digest
of a compact JSON array with non-ASCII characters escaped. In order, its fields
are the uppercased HTTP method, exact path template, case name, source kind,
rule identifier, and field location. A nonempty authentication profile name is
appended when present. An absent field is JSON `null`, and the root JSON pointer
is the empty string. Operation IDs, fixture filenames, run IDs, assertion
order, verdicts, and message text are outside the fingerprint.

### Run the comparison in GitHub Actions

After collecting your current report, use the EndNext composite Action on a Linux or macOS runner. Replace `ENDNEXT_COMMIT_SHA` with the full SHA of an EndNext commit containing the Action. Paths are relative to the workflow workspace, and absolute paths also work. The Action installs EndNext from its pinned revision, so the step works before a PyPI release is available.

```yaml
- name: Compare EndNext evidence
  id: endnext
  uses: Minells/EndNext@ENDNEXT_COMMIT_SHA
  with:
    baseline: reports/baseline.json
    current: reports/current.json
    artifact-name: endnext-diff
```

The step uploads `diff.json` before applying the gate. Invalid inputs produce `diagnostics.txt`. Use the `exit-code` output to distinguish a blocking finding (`1`) from incomplete verification (`2`). The `artifact-id` output identifies the upload. Both outcomes fail the step by default. When generating the reports in an earlier step, handle the `check` or `scan` exit code so the workflow can reach the comparison when valid JSON evidence is available.

### Set a temporary finding exception

Use `endnext diff baseline.json current.json --format json` to find a failure's `current.fingerprint`. Copy it to a local TOML policy and set the first UTC date on which the failure must block again. For example, an `expires_on` of `2027-01-15` is active through January 14 UTC.

```toml
[exceptions."sha256:0000000000000000000000000000000000000000000000000000000000000000"]
reason = "Track the schema correction in issue #42"
expires_on = 2027-01-15
```

Run `endnext diff baseline.json current.json --exceptions exceptions.toml`, or pass `exceptions: exceptions.toml` in the Action's `with` block. Use a reviewed policy file and keep exceptions in it until the finding is resolved. The comparison records the reason, expiry, and whether the exception is active or expired alongside the original finding. An active exception waives only the matching failure. A matching failure blocks when its exception expires, even if it is in the baseline. Incomplete checks continue to affect the result.

## Documentation

| Resource | What you’ll find |
| --- | --- |
| [Examples](examples/) | Sample contracts, fixtures, TOML suites, and the local demo service |
| [Validator notes](docs/VALIDATOR.md) | Supported inputs, schema validation decisions, and limitations |
| [Python API v1](docs/PYTHON_API.md) | Verify multiple local response fixtures against one loaded contract |
| [Offline benchmark](docs/BENCHMARK.md) | Reproduce the synthetic accuracy and contract reuse measurements |
| [Migration guide](docs/MIGRATING.md) | Upgrade saved evidence and verify supported workflows on each platform |
| [Changelog](CHANGELOG.md) | User-facing changes and pull requests |
| [Release candidate verification](docs/RELEASE_CANDIDATE.md) | Packaging checks and results from independent projects |
| [Brand guidelines](docs/BRANDING.md) | Official logos, design tokens, and documentation conventions |
| [Security policy](SECURITY.md) | Test scope and private vulnerability reporting |

## Contributing

Follow the [contribution guide](CONTRIBUTING.md) to set up the development environment and run checks. CI verifies linting, types, tests with a 96% combined statement and branch coverage floor, and installation from both wheel and source distribution on Linux and macOS with Python 3.12. A Windows job installs the wheel and verifies offline workflows. Pull requests into `main` require the **EndNext merge gate**.

When a change affects CLI behavior, report formats, or a user workflow, update the relevant README sections, examples, or guides in the same pull request and verify the commands you changed.

## License

EndNext is available under the [MIT License](LICENSE).
