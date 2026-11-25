"""Acquire observations for the live cases admitted by the offline planner."""

from dataclasses import dataclass
from typing import Literal

from .contract import Contract
from .evidence import Assertion, Operation, Verdict, primary
from .inputs import BodyState, Fixture, InputError, Json, decode_json_value, walk
from .planner import Case, PlanConfig, PlannedCase, build_plan
from .rules import verify
from .transport import Acquisition, LoopbackTransport, Target


@dataclass(frozen=True)
class ScanCase:
    case: str
    operation: Operation
    source: Literal["live_http", "imported_fixture"]
    state: Verdict
    reason: str
    status: int | None = None
    media_type: str | None = None
    assertion: Assertion | None = None
    assertions: tuple[Assertion, ...] = ()


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
        return _skipped(case, operation, source, planned.reason)
    if case.source == "fixture":
        return _skipped(case, operation, source, "offline_fixture")
    request = transport.target.request(case)
    if request is None:
        return _skipped(case, operation, source, "outside_target_scope")
    capture = transport.acquire(request)
    return _check_capture(contract, operation, case, capture)


def _skipped(
    case: Case,
    operation: Operation,
    source: Literal["live_http", "imported_fixture"],
    reason: str,
) -> ScanCase:
    assertion = Assertion(
        "case.preconditions", "SKIPPED", "An eligible live case.", reason
    )
    return ScanCase(
        case.name,
        operation,
        source,
        "SKIPPED",
        reason,
        assertion=assertion,
        assertions=(assertion,),
    )


def _check_capture(
    contract: Contract,
    operation: Operation,
    case: Case,
    capture: Acquisition,
) -> ScanCase:
    if capture.status is None:
        problem = capture.problem or "response_status_missing"
        state: Verdict = (
            "SKIPPED"
            if problem
            in {
                "outside_target_scope",
                "request_budget_exhausted",
                "run_deadline_exhausted",
            }
            else "ERROR"
        )
        assertion = Assertion(
            "http.acquire", state, "A bounded HTTP observation.", problem
        )
        return ScanCase(
            case.name,
            operation,
            "live_http",
            state,
            problem,
            assertion=assertion,
            assertions=(assertion,),
        )

    body: Json = None
    body_state: BodyState = "absent"
    if capture.problem is not None:
        body_state = "truncated"
    elif capture.body:
        body_state = "complete"
        if capture.media_type == "application/json":
            try:
                body = decode_json_value(capture.body, "response")
                for _ in walk(body, "response"):
                    pass
            except InputError:
                body_state = "invalid"
    fixture = Fixture(
        case.name,
        operation.method.lower(),
        operation.path_template,
        {name: str(value) for name, value in case.path_parameters.items()},
        capture.status,
        body,
        "",
        capture.media_type,
        body_state,
        case.expected_status,
    )
    try:
        _, assertions = verify(contract, fixture, imported=False)
    except InputError:
        assertions = (
            Assertion(
                "response.body.schema",
                "INCONCLUSIVE",
                "A documented response schema.",
                "response_schema_unavailable",
            ),
        )
    except Exception:
        assertions = (
            Assertion(
                "http.verify",
                "ERROR",
                "A completed verification.",
                "verification_error",
            ),
        )
    if capture.problem in {"request_timeout", "transport_error"}:
        assertions += (
            Assertion(
                "http.acquire", "ERROR", "A bounded HTTP observation.", capture.problem
            ),
        )
    selected = primary(assertions)
    return ScanCase(
        case.name,
        operation,
        "live_http",
        selected.verdict,
        capture.problem or selected.reason,
        capture.status,
        capture.media_type,
        selected,
        assertions,
    )
