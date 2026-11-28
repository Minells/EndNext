# Offline Python API v1

Python extensions can validate captured responses through `endnext.api_v1`.
Install EndNext in the extension's environment. Load a local OpenAPI 3.1
contract with `FixtureChecker(contract)` and call `check(fixture)` for each
local JSON response fixture. Both paths accept `str` or `pathlib.Path`. Each
checker validates the contract once for sequential use. Create a new checker
when the contract changes.

```python
import json

from endnext.api_v1 import FixtureChecker

checker = FixtureChecker("examples/items.openapi.json")
for fixture in ("examples/item-pass.json", "examples/item-fail.json"):
    result = checker.check(fixture)
    print(json.dumps(result.to_report()))
```

`CheckResult` contains the operation, its assertions, and observation metadata.
`assertion` gives the primary `Assertion`, and `exit_code` uses the same 0, 1,
and 2 outcomes as `endnext check`. Each assertion has a rule identifier, a
`Verdict`, an expectation, a reason, and optional field and observed type
metadata. `to_report()` returns the v2 evidence dictionary used by the CLI,
with a fresh run ID on each call. Save it as JSON for `endnext report` or
`endnext diff`. Use shareable case names and fixture filenames.

Invalid inputs raise `InputError`. An unexpected failure during loading or
verification raises `ExecutionError` with a sanitized message. Keep response
bodies and credential values in the local fixtures. The result carries only
the evidence fields used by the CLI, and the checker runs without network I/O.

Run the [batch example](../examples/check_fixtures.py) from an installed checkout.
It writes one report per line for consumers that need to handle several results.

```sh
python examples/check_fixtures.py examples/items.openapi.json \
  examples/item-pass.json examples/item-fail.json
```

The example returns the highest exit code across its observations. For the
bundled pair it returns `1` and writes both reports. Import extension code
explicitly from your own application. The API version is the `api_v1` module
name, independent of the package version and the report schema version.

For externally supplied contracts use `IsolatedFixtureChecker(contract).check(fixture)`.
Each call reads the files afresh in a child process and returns a v2 report.
A 30-second deadline raises `ExecutionError`. The checker can be reused after a
timeout. Use `FixtureChecker` for sequential checks of a trusted, cached contract.
