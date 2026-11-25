"""Acquire observations for the live cases admitted by the offline planner."""

from dataclasses import dataclass
from typing import Literal

from .contract import Contract
from .evidence import Assertion, Operation
from .inputs import Fixture, InputError, decode_json_value, walk
from .planner import Case, PlanConfig, PlannedCase, build_plan
from .transport import Acquisition, LoopbackTransport, Target

type State = Literal["PASS", "FAIL", "SKIPPED", "INCONCLUSIVE", "ERROR"]


@dataclass(frozen=True)
class ScanCase:
    case: str
    operation: Operation
    source: Literal["live_http", "imported_fixture"]
    state: State
    reason: str
    status: int | None = None
    media_type: str | None = None
    assertion: Assertion | None = None


@dataclass(frozen=True)
class ScanRun:
    request_budget: int
    requests_attempted: int
    requests_sent: int
    cases: tuple[ScanCase, ...]

    @property
    def exit_code(self) -> int:
        states = {case.state for case in self.cases}
        if not self.requests_attempted or states.intersection(
            {"ERROR", "INCONCLUSIVE"}
        ):
            return 2
        return int("FAIL" in states)


def scan(contract: Contract, config: PlanConfig) -> ScanRun:
    target = Target.load(config.target)
    plan = build_plan(
        contract.inventory(),
        config,
        allows_http=lambda case: target.request(case) is not None,
    )
    configured = {case.name: case for case in config.cases}
    outcomes = []
    transport = LoopbackTransport(target, plan.request_budget)
    try:
        for entry in plan.operations:
            for case in entry.cases:
                outcomes.append(
                    _scan_case(
                        contract,
                        transport,
                        entry.operation,
                        case,
                        configured[case.name],
                    )
                )
    finally:
        transport.close()
    return ScanRun(
        plan.request_budget, transport.attempted, transport.sent, tuple(outcomes)
    )


def _scan_case(
    contract: Contract,
    transport: LoopbackTransport,
    operation: Operation,
    planned: PlannedCase,
    case: Case,
) -> ScanCase:
    source: Literal["live_http", "imported_fixture"] = (
        "live_http" if case.source == "http" else "imported_fixture"
    )
    if planned.reason is not None:
        return ScanCase(case.name, operation, source, "SKIPPED", planned.reason)
    if case.source == "fixture":
        return ScanCase(case.name, operation, source, "SKIPPED", "offline_fixture")
    request = transport.target.request(case)
    if request is None:
        return ScanCase(case.name, operation, source, "SKIPPED", "outside_target_scope")
    capture = transport.acquire(request)
    return _check_capture(contract, operation, case, capture)


def _check_capture(
    contract: Contract, operation: Operation, case: Case, capture: Acquisition
) -> ScanCase:
    state: State
    if capture.problem is not None:
        state = "SKIPPED"
        if capture.problem in {"request_timeout", "transport_error"}:
            state = "ERROR"
        if capture.problem in {"response_body_limit", "unsupported_content_encoding"}:
            state = "INCONCLUSIVE"
        return ScanCase(
            case.name,
            operation,
            "live_http",
            state,
            capture.problem,
            capture.status,
            capture.media_type,
        )
    if capture.status is None:
        return ScanCase(
            case.name, operation, "live_http", "ERROR", "response_status_missing"
        )
    if capture.media_type != "application/json" or capture.body is None:
        return ScanCase(
            case.name,
            operation,
            "live_http",
            "INCONCLUSIVE",
            "json_body_unavailable",
            capture.status,
            capture.media_type,
        )
    try:
        body = decode_json_value(capture.body, "response")
        for _ in walk(body, "response"):
            pass
    except InputError:
        return ScanCase(
            case.name,
            operation,
            "live_http",
            "INCONCLUSIVE",
            "invalid_json_body",
            capture.status,
            capture.media_type,
        )
    fixture = Fixture(
        case.name,
        operation.method.lower(),
        operation.path_template,
        {name: str(value) for name, value in case.path_parameters.items()},
        capture.status,
        body,
        "",
    )
    try:
        _, assertion = contract.check(fixture)
    except InputError:
        return ScanCase(
            case.name,
            operation,
            "live_http",
            "INCONCLUSIVE",
            "response_schema_unavailable",
            capture.status,
            capture.media_type,
        )
    except Exception:
        return ScanCase(
            case.name,
            operation,
            "live_http",
            "ERROR",
            "verification_error",
            capture.status,
            capture.media_type,
        )
    return ScanCase(
        case.name,
        operation,
        "live_http",
        assertion.verdict,
        assertion.reason,
        capture.status,
        capture.media_type,
        assertion,
    )
