"""Measure offline accuracy and contract reuse on synthetic OpenAPI fixtures."""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

from endnext.api_v1 import FixtureChecker
from endnext.contract import Contract

EXAMPLES = Path(__file__).resolve().parent
SCHEMA = "response.body.schema"
STATUS = "response.status.documented"
MEDIA = "response.media_type.documented"
EXPECTED_STATUS = "response.status.expected"
ACCESS = "security.anonymous_access"
CACHE = "security.cache_directive"
type Verdicts = dict[tuple[str, str], str]


def make_dataset(
    directory: Path, operations: int
) -> tuple[Path, tuple[Path, ...], Verdicts]:
    document = json.loads((EXAMPLES / "items.openapi.json").read_text(encoding="utf-8"))
    template = document["paths"]["/items/{id}"]
    template["get"]["responses"]["403"] = {"description": "Access denied"}
    for index in range(1, operations):
        operation = deepcopy(template)
        operation["get"]["operationId"] = f"getCatalogItem{index}"
        document["paths"][f"/catalog/{index}/{{id}}"] = operation
    contract = directory / "contract.json"
    contract.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")

    passing = EXAMPLES / "item-pass.json"
    policy_failure = EXAMPLES / "item-policy-fail.json"
    partial = json.loads(passing.read_text(encoding="utf-8"))
    del partial["body"]
    partial["body_state"] = "truncated"
    denial = json.loads(policy_failure.read_text(encoding="utf-8"))
    del denial["body"], denial["media_type"]
    denial.update(
        status=403, body_state="absent", headers={"Cache-Control": "no-store"}
    )

    partial_path = directory / "item-partial.json"
    partial_path.write_text(json.dumps(partial), encoding="utf-8")
    denial_path = directory / "item-policy-pass.json"
    denial_path.write_text(json.dumps(denial), encoding="utf-8")

    normal = {SCHEMA: "PASS", STATUS: "PASS", MEDIA: "PASS", EXPECTED_STATUS: "SKIPPED"}
    cases = {
        passing: normal,
        EXAMPLES / "item-fail.json": normal | {SCHEMA: "FAIL"},
        partial_path: normal | {SCHEMA: "INCONCLUSIVE"},
        policy_failure: normal | {ACCESS: "FAIL", CACHE: "WARN"},
        denial_path: normal
        | {SCHEMA: "SKIPPED", MEDIA: "SKIPPED", ACCESS: "PASS", CACHE: "PASS"},
    }
    oracle = {
        (path.name, rule): verdict
        for path, rules in cases.items()
        for rule, verdict in rules.items()
    }
    return contract, tuple(cases), oracle


def assess(
    contract: Path, fixtures: tuple[Path, ...], *, reuse: bool
) -> tuple[Verdicts, float]:
    start = perf_counter()
    checker = FixtureChecker(contract) if reuse else None
    verdicts: Verdicts = {}
    for path in fixtures:
        result = (checker if checker is not None else FixtureChecker(contract)).check(
            path
        )
        for assertion in result.assertions:
            key = (path.name, assertion.rule)
            if key in verdicts:
                raise ValueError("The benchmark observed a duplicate assertion.")
            verdicts[key] = assertion.verdict
    return verdicts, perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.operations <= 500 or not 1 <= args.rounds <= 10:
        parser.error("Choose 1 to 500 operations and 1 to 10 rounds.")

    reload_times = []
    reuse_times = []
    repeatable = True
    baseline = None
    with TemporaryDirectory() as directory:
        contract, fixtures, oracle = make_dataset(Path(directory), args.operations)
        inventory = len(Contract.load(contract).inventory().operations)
        for _ in range(args.rounds):
            reloaded, reload_time = assess(contract, fixtures, reuse=False)
            reused, reuse_time = assess(contract, fixtures, reuse=True)
            if baseline is None:
                baseline = reloaded
            repeatable &= reloaded == reused == baseline
            reload_times.append(reload_time)
            reuse_times.append(reuse_time)

    assert baseline is not None
    mismatches = [
        {
            "fixture": key[0],
            "rule": key[1],
            "expected": oracle.get(key),
            "observed": baseline.get(key),
        }
        for key in sorted(oracle.keys() | baseline.keys())
        if oracle.get(key) != baseline.get(key)
    ]
    print(
        json.dumps(
            {
                "operations": inventory,
                "rounds": args.rounds,
                "fixtures": len(fixtures),
                "assertions": len(oracle),
                "repeatable": repeatable,
                "false_positives": sum(
                    item["observed"] == "FAIL" for item in mismatches
                ),
                "false_negatives": sum(
                    item["expected"] == "FAIL" for item in mismatches
                ),
                "mismatches": mismatches,
                "median_seconds": {
                    "reload": round(median(reload_times), 6),
                    "reuse": round(median(reuse_times), 6),
                },
            },
            indent=2,
        )
    )
    return int(bool(mismatches) or not repeatable or inventory != args.operations)


if __name__ == "__main__":
    raise SystemExit(main())
