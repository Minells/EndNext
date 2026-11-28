"""Acquire observations for the live cases admitted by the offline planner."""

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .contract import Contract
from .evidence import Assertion, Operation, Verdict, primary
from .inputs import (
    BodyState,
    Fixture,
    InputError,
    Json,
    decode_json_value,
    is_json_media_type,
    walk,
)
from .planner import AUTHORIZATION_RULE, Case, PlanConfig, PlannedCase, build_plan
from .policy import Target
from .resilience_evidence import ClientObservation
from .rules import verify
from .transport import Acquisition, LoopbackTransport

_BEARER = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_UNSENT = {"outside_target_scope", "request_budget_exhausted", "run_deadline_exhausted"}
_INTERRUPTED = {"request_timeout", "transport_error"}
type RecordCapture = Callable[[str, Acquisition, int], None]


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
    auth_profile: str | None = None


@dataclass(frozen=True)
class ScanMeasurement:
    case: str
    observation: ClientObservation


@dataclass(frozen=True)
class ScanRun:
    request_budget: int
    requests_attempted: int
    requests_sent: int
    cases: tuple[ScanCase, ...]
    measurement: ScanMeasurement | None = None

    @property
    def exit_code(self) -> int:
        states = {case.state for case in self.cases}
        if (
            not self.requests_attempted
            or states.intersection({"ERROR", "INCONCLUSIVE"})
            or any(
                case.auth_profile is not None and case.state == "SKIPPED"
                for case in self.cases
            )
        ):
            return 2
        return int("FAIL" in states)


def scan(
    contract: Contract,
    config: PlanConfig,
    *,
    proxy_run_id: str | None = None,
    bearer_grants: frozenset[tuple[str, str, str]] = frozenset(),
) -> ScanRun:
    target = Target.load(config.target)
    plan = build_plan(
        contract.inventory(),
        config,
        allows_http=lambda case: target.request(case) is not None,
    )
    for operation in plan.operations:
        for planned in operation.cases:
            profile = planned.auth_profile
            if (
                planned.reason is None
                and profile is not None
                and (
                    profile,
                    config.auth_profiles[profile],
                    str(target.origin).rstrip("/"),
                )
                not in bearer_grants
            ):
                raise InputError(
                    "A planned bearer profile lacks a matching --allow-bearer grant."
                )
    if proxy_run_id is not None and plan.requests_planned != 1:
        raise InputError(
            "A correlated scan requires exactly one eligible live request."
        )

    measurement = None

    def record(name: str, capture: Acquisition, elapsed_ms: int) -> None:
        nonlocal measurement
        assert proxy_run_id is not None
        status = capture.status
        if capture.problem is not None or status is None or not 200 <= status <= 599:
            status = None
        measurement = ScanMeasurement(
            name, ClientObservation(proxy_run_id, status, elapsed_ms)
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
                        config.auth_profiles,
                        record if proxy_run_id is not None else None,
                    )
                )
    finally:
        transport.close()
    return ScanRun(
        plan.request_budget,
        transport.attempted,
        transport.sent,
        tuple(outcomes),
        measurement,
    )


def _scan_case(
    contract: Contract,
    transport: LoopbackTransport,
    operation: Operation,
    planned: PlannedCase,
    case: Case,
    profiles: dict[str, str],
    record: RecordCapture | None,
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
    token = None
    if case.auth_profile is not None:
        token = os.environ.get(profiles[case.auth_profile])
        if not token:
            return _skipped(case, operation, source, "auth_credential_missing")
        if len(token) > 4096 or not _BEARER.fullmatch(token):
            return _authorization_outcome(
                case, operation, "ERROR", "auth_credential_invalid"
            )
    started = time.monotonic_ns() if record is not None else 0
    capture = transport.acquire(request, bearer_token=token)
    if record is not None and transport.sent:
        elapsed_ms = (time.monotonic_ns() - started + 999_999) // 1_000_000
        record(case.name, capture, elapsed_ms)
    if case.auth_profile is not None:
        return _check_authorization(case, operation, capture)
    return _check_capture(contract, operation, case, capture)


def _skipped(
    case: Case,
    operation: Operation,
    source: Literal["live_http", "imported_fixture"],
    reason: str,
) -> ScanCase:
    if case.auth_profile is not None:
        return _authorization_outcome(case, operation, "SKIPPED", reason)
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


def _authorization_outcome(
    case: Case,
    operation: Operation,
    state: Verdict,
    reason: str,
    status: int | None = None,
) -> ScanCase:
    assertion = Assertion(
        AUTHORIZATION_RULE,
        state,
        f"Authenticated access: {case.expected_access}.",
        reason,
    )
    return ScanCase(
        case.name,
        operation,
        "live_http",
        state,
        reason,
        status=status,
        assertion=assertion,
        assertions=(assertion,),
        auth_profile=case.auth_profile,
    )


def _check_authorization(
    case: Case, operation: Operation, capture: Acquisition
) -> ScanCase:
    # Authenticated responses may reflect a credential in their headers or body.
    status = capture.status
    problem = capture.problem
    state: Verdict
    if problem in _UNSENT:
        state = "SKIPPED"
    elif status is None or problem in _INTERRUPTED:
        state = "ERROR"
    elif problem is not None:
        state = "INCONCLUSIVE"
    elif 200 <= status <= 299 or status == 403:
        observed = "deny" if status == 403 else "allow"
        state = "PASS" if observed == case.expected_access else "FAIL"
        return _authorization_outcome(
            case,
            operation,
            state,
            f"Expected {case.expected_access}. The service returned HTTP {status}.",
            status,
        )
    else:
        state = "INCONCLUSIVE"
        return _authorization_outcome(
            case,
            operation,
            state,
            f"HTTP {status} does not establish an authenticated access outcome.",
            status,
        )
    return _authorization_outcome(
        case, operation, state, problem or "response_status_missing", status
    )


def _check_capture(
    contract: Contract,
    operation: Operation,
    case: Case,
    capture: Acquisition,
) -> ScanCase:
    if capture.status is None:
        problem = capture.problem or "response_status_missing"
        state: Verdict = "SKIPPED" if problem in _UNSENT else "ERROR"
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
        if is_json_media_type(capture.media_type):
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
    if capture.problem in _INTERRUPTED:
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
