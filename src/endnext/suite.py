"""Evaluate the imported cases in a TOML plan with one contract load."""

import json
from dataclasses import asdict, dataclass, replace
from functools import cache
from uuid import uuid4

from .contract import Contract
from .coverage import Coverage, OperationCoverage
from .evidence import Assertion, Operation, counts
from .inputs import MAX_BYTES, Fixture, InputError
from .planner import Case, PlanConfig, PlannedCase, build_plan, load_plan_fixture
from .reporting import SavedReport, render
from .rules import verify

_EXECUTED = {"PASS", "FAIL", "WARN"}
_INTENTIONAL_SKIPS = {"operation_not_selected", "suite_http_skipped"}
_REMEDIES = {
    "fixture_missing": "Set fixture to a readable local JSON file.",
    "fixture_invalid": "Check the fixture fields with endnext check.",
    "fixture_operation_mismatch": "Use a fixture for the method and path in the plan.",
    "operation_not_selected": "The plan does not select this operation.",
    "suite_http_skipped": "Suite only evaluates saved responses. Supply a fixture to verify this case offline.",
}


@dataclass(frozen=True)
class SuiteCase:
    case: str
    operation: Operation
    source: dict[str, str]
    assertions: tuple[Assertion, ...]
    status: int | None = None
    media_type: str | None = None

    def to_report(self) -> dict[str, object]:
        return {
            "case": self.case,
            "operation": asdict(self.operation),
            "source": self.source,
            "observation": {"status": self.status, "media_type": self.media_type},
            "assertions": [asdict(assertion) for assertion in self.assertions],
        }


def _skipped(planned: PlannedCase, operation: Operation, reason: str) -> SuiteCase:
    intentional = reason in _INTENTIONAL_SKIPS
    source = "live_http" if planned.source == "http" else "imported_fixture"
    return SuiteCase(
        planned.name,
        operation,
        {"kind": source},
        (
            Assertion(
                "suite.case" if intentional else "fixture.acquire",
                "SKIPPED" if intentional else "INCONCLUSIVE",
                "A valid local response fixture for the planned operation.",
                f"{reason}. {_REMEDIES[reason]}",
            ),
        ),
    )


def _verify_case(
    contract: Contract, case: Case, fixture: Fixture, operation: Operation
) -> SuiteCase:
    source = {"kind": "imported_fixture", "fixture": fixture.filename}
    assertions: tuple[Assertion, ...]
    if (
        case.expected_status is not None
        and fixture.expected_status is not None
        and case.expected_status != fixture.expected_status
    ):
        assertions = (
            Assertion(
                "fixture.expectations",
                "INCONCLUSIVE",
                "Matching plan and fixture expected_status fields.",
                "Align the plan and fixture expected_status fields or omit one.",
                "expected_status",
            ),
        )
    else:
        expected = case.expected_status
        if expected is not None:
            fixture = replace(fixture, expected_status=expected)
        try:
            _, assertions = verify(contract, fixture, imported=True)
        except InputError as error:
            assertions = (
                Assertion(
                    "fixture.verify",
                    "INCONCLUSIVE",
                    "A response schema that can be applied.",
                    str(error),
                ),
            )
    return SuiteCase(
        case.name, operation, source, assertions, fixture.status, fixture.media_type
    )


def run_suite(
    contract: Contract, config: PlanConfig, engine_version: str, output_format: str
) -> int:
    load_fixture = cache(load_plan_fixture)
    plan = build_plan(
        contract.inventory(), config, offline_only=True, load_fixture=load_fixture
    )
    configured = {case.name: case for case in config.cases}
    outcomes = []
    operations = []
    for item in plan.operations:
        operation_outcomes = []
        for planned in item.cases:
            if planned.reason is not None:
                operation_outcomes.append(
                    _skipped(planned, item.operation, planned.reason)
                )
                continue
            case = configured[planned.name]
            assert case.fixture is not None
            fixture = load_fixture(case.fixture)
            assert isinstance(fixture, Fixture)
            operation_outcomes.append(
                _verify_case(contract, case, fixture, item.operation)
            )

        outcomes.extend(operation_outcomes)
        if item.selected:
            assertions = [
                assertion
                for case in operation_outcomes
                for assertion in case.assertions
            ]
            executed = any(assertion.verdict in _EXECUTED for assertion in assertions)
            incomplete = not executed or any(
                assertion.verdict in {"ERROR", "INCONCLUSIVE"}
                or (assertion.rule == "suite.case" and assertion.verdict == "SKIPPED")
                for assertion in assertions
            )
        else:
            executed = incomplete = False
        operations.append(
            OperationCoverage(item.operation, item.selected, executed, incomplete)
        )

    totals = counts(assertion for case in outcomes for assertion in case.assertions)
    report = {
        "schema_version": "2",
        "engine_version": engine_version,
        "run_id": str(uuid4()),
        "contract_digest": f"sha256:{contract.digest}",
        "cases": [case.to_report() for case in outcomes],
        "counts": totals,
        "coverage": asdict(Coverage(tuple(operations))),
    }
    payload = json.dumps(report, separators=(",", ":"))
    if len(payload) + 1 > MAX_BYTES:
        raise InputError(
            "The suite report exceeds 1 MiB. Split the plan into smaller suites."
        )
    if output_format == "json":
        print(payload)
    else:
        print(render(SavedReport.from_document(json.loads(payload)), markdown=False))
    if totals["FAIL"]:
        return 1
    return (
        2
        if (
            not any(totals[verdict] for verdict in _EXECUTED)
            or totals["ERROR"]
            or totals["INCONCLUSIVE"]
        )
        else 0
    )
