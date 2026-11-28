# Offline accuracy and performance benchmark

Install EndNext from the checkout and run the synthetic benchmark with Python
3.12 or later.

```sh
python examples/benchmark_offline.py --operations 500 --rounds 3
```

The script writes a temporary OpenAPI 3.1 contract and local fixtures. The
contract has one item operation plus copies with distinct paths and operation
IDs up to the requested inventory size. It also declares an HTTP 403 response
for the authorization control. All inputs are synthetic and the benchmark
opens no network connections.

| Observation | Expected result |
| --- | --- |
| A conforming item response | The schema, status, and media type pass |
| An item with a string quantity | The schema fails |
| A truncated body | The schema is inconclusive |
| A sensitive item returned to a caller expected to be denied | The access assertion fails and the missing `no-store` directive warns |
| A 403 with `no-store` for that restricted operation | The access and cache assertions pass and the body checks are skipped |

The 24 expected rule verdicts include the unaffected assertions. The benchmark
reports a false positive when it produces `FAIL` for a rule whose expected
verdict differs, and a false negative when an expected `FAIL` is missing or has
another verdict. Every disagreement is listed with its fixture and rule.
Repeated rounds and both execution strategies must produce identical verdicts.
A mismatch, a divergent round, or an incorrect inventory count returns exit
code `1`.

`median_seconds.reload` measures five checks with a separate contract load for
each fixture. `median_seconds.reuse` measures the same checks with one
`FixtureChecker`. Both include fixture reads and at least one contract load.
Timing excludes process startup and JSON formatting. CI verifies the dataset
and inventory on a small run. Compare timing on the same machine and Python
version using the same options.

One macOS arm64 run with Python 3.12.13 verified all 24 verdicts across three
rounds at 500 operations. Median time for the five checks was 6.83 seconds
with a reload for each fixture and 1.39 seconds when reusing the contract.

The suite milestone repeated the benchmark with Python 3.12.13 on macOS arm64
using `--operations 500 --rounds 3`. It verified the same five fixtures and
24 assertions with zero disagreements. Median time was 6.851 seconds with
reloading and 1.393 seconds with reuse, a 4.9× speedup.

The suite reuses the rule engine behind both fixture checkers. The documented
return types stay stable. `FixtureChecker.check` returns a typed
`CheckResult`, and `IsolatedFixtureChecker.check` returns a JSON-compatible
dictionary from a separately bounded process. Suite runs multiple checks
inside one bounded CLI worker to avoid restarting the process and reloading
the contract for every fixture.
