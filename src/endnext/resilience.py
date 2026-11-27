"""Replay deterministic dependency outcomes through an in-process HTTP transport."""

import hashlib
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from .evidence import Assertion, Operation, primary
from .inputs import CASE_NAME, InputError, read_file

OPERATION = Operation("GET", "/dependency", None)
_ORIGIN = "http://dependency.invalid"
_URL = httpx.URL(_ORIGIN + OPERATION.path_template)
_MAX_ATTEMPTS = 20
_MAX_DELAY_MS = 5_000
_MAX_ELAPSED_MS = 60_000

type Outcome = int | Literal["timeout", "disconnect"]


@dataclass(frozen=True)
class Step:
    outcome: Outcome
    delay_ms: int


@dataclass(frozen=True)
class Scenario:
    name: str
    expected_status: int
    min_attempts: int
    max_attempts: int
    recovery_budget_ms: int
    steps: tuple[Step, ...]
    digest: str

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        raw = read_file(path, "resilience scenario")
        try:
            data = tomllib.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            raise InputError(
                "The resilience scenario contains invalid UTF-8 TOML."
            ) from None
        if data.keys() - {
            "name",
            "expected_status",
            "min_attempts",
            "max_attempts",
            "recovery_budget_ms",
            "steps",
        }:
            raise InputError("The resilience scenario contains an unsupported option.")

        name = data.get("name")
        if not isinstance(name, str) or not CASE_NAME.fullmatch(name):
            raise InputError("The resilience scenario requires a valid name.")
        status = _integer(data.get("expected_status"), "expected_status", 200, 299)
        minimum = _integer(data.get("min_attempts"), "min_attempts", 1, _MAX_ATTEMPTS)
        maximum = _integer(
            data.get("max_attempts"), "max_attempts", minimum, _MAX_ATTEMPTS
        )
        budget = _integer(
            data.get("recovery_budget_ms"), "recovery_budget_ms", 0, _MAX_ELAPSED_MS
        )
        steps = data.get("steps")
        if not isinstance(steps, list) or not minimum <= len(steps) <= _MAX_ATTEMPTS:
            raise InputError(
                "The scenario needs at least min_attempts steps and at most 20."
            )
        parsed = tuple(_parse_step(step) for step in steps)
        if sum(step.delay_ms for step in parsed) > _MAX_ELAPSED_MS:
            raise InputError("The scenario exceeds 60000 ms of simulated latency.")
        return cls(
            name,
            status,
            minimum,
            maximum,
            budget,
            parsed,
            hashlib.sha256(raw).hexdigest(),
        )


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(
            f"Scenario {field} must be an integer from {minimum} to {maximum}."
        )
    return value


def _parse_step(raw: object) -> Step:
    if not isinstance(raw, dict) or raw.keys() - {"outcome", "delay_ms"}:
        raise InputError(
            "Each scenario step allows an outcome and an optional delay_ms."
        )
    outcome = raw.get("outcome")
    if outcome not in ("timeout", "disconnect"):
        outcome = _integer(outcome, "step outcome", 200, 599)
    delay = _integer(raw.get("delay_ms", 0), "step delay_ms", 0, _MAX_DELAY_MS)
    return Step(outcome, delay)


@dataclass(frozen=True)
class Event:
    attempt: int
    outcome: Outcome
    elapsed_ms: int


class _Dependency:
    def __init__(self, steps: tuple[Step, ...]):
        self.steps = steps
        self.events: list[Event] = []
        self.elapsed_ms = 0
        self.problem: str | None = None

    def respond(self, request: httpx.Request) -> httpx.Response:
        index = len(self.events)
        if self.problem is None:
            if request.method != OPERATION.method or request.url != _URL:
                self.problem = (
                    "The client requested an operation outside the simulation."
                )
            elif index == len(self.steps):
                self.problem = (
                    "The client requested more outcomes than the scenario provides."
                )
        if self.problem is not None:
            raise httpx.TransportError(self.problem, request=request)

        step = self.steps[index]
        self.elapsed_ms += step.delay_ms
        self.events.append(Event(index + 1, step.outcome, self.elapsed_ms))
        if step.outcome == "timeout":
            raise httpx.ReadTimeout("Simulated timeout.", request=request)
        if step.outcome == "disconnect":
            raise httpx.ReadError("Simulated disconnect.", request=request)
        return httpx.Response(step.outcome)


@dataclass(frozen=True)
class ResilienceRun:
    events: tuple[Event, ...]
    final_status: int | None
    elapsed_ms: int
    assertions: tuple[Assertion, ...]

    @property
    def exit_code(self) -> int:
        verdict = primary(self.assertions).verdict
        if verdict in {"ERROR", "INCONCLUSIVE"}:
            return 2
        return int(verdict == "FAIL")


def run_scenario(
    scenario: Scenario, client: Callable[[httpx.Client], int | None]
) -> ResilienceRun:
    """Run a synchronous client against GET /dependency using a network-free HTTPX client."""
    dependency = _Dependency(scenario.steps)
    unexpected = False
    result = None
    with httpx.Client(
        base_url=_ORIGIN,
        transport=httpx.MockTransport(dependency.respond),
        trust_env=False,
        follow_redirects=False,
    ) as session:
        try:
            result = client(session)
        except (httpx.ReadTimeout, httpx.ReadError):
            # A synthetic transport fault can terminate the client without a status.
            pass
        except Exception:
            unexpected = True

    if result is not None and (type(result) is not int or not 200 <= result <= 599):
        unexpected = True

    expectation = "The client completes a scripted dependency interaction."
    assertions: tuple[Assertion, ...]
    if dependency.problem is not None or unexpected:
        assertions = (
            Assertion(
                "resilience.execution",
                "ERROR",
                expectation,
                dependency.problem or "The client could not complete the simulation.",
            ),
        )
        result = None
    elif not dependency.events:
        assertions = (
            Assertion(
                "resilience.execution",
                "INCONCLUSIVE",
                expectation,
                "No dependency attempts were observed.",
            ),
        )
        result = None
    else:
        assertions = _evaluate(
            scenario, result, len(dependency.events), dependency.elapsed_ms
        )
    return ResilienceRun(
        tuple(dependency.events), result, dependency.elapsed_ms, assertions
    )


def _evaluate(
    scenario: Scenario, result: int | None, attempts: int, elapsed_ms: int
) -> tuple[Assertion, ...]:
    recovered = result == scenario.expected_status
    status = Assertion(
        "resilience.final_status",
        "PASS" if recovered else "FAIL",
        f"The client returns HTTP {scenario.expected_status}.",
        f"The client returned HTTP {result}."
        if result is not None
        else "The client returned no HTTP status.",
    )
    retry = Assertion(
        "resilience.attempts",
        "PASS"
        if scenario.min_attempts <= attempts <= scenario.max_attempts
        else "FAIL",
        f"From {scenario.min_attempts} to {scenario.max_attempts} dependency attempts.",
        f"Observed {attempts} dependency attempts.",
    )
    recovery_expectation = (
        f"Recovery within {scenario.recovery_budget_ms} ms of simulated latency."
    )
    if recovered:
        time = Assertion(
            "resilience.recovery_time",
            "PASS" if elapsed_ms <= scenario.recovery_budget_ms else "FAIL",
            recovery_expectation,
            f"Observed recovery after {elapsed_ms} ms of simulated latency.",
        )
    else:
        time = Assertion(
            "resilience.recovery_time",
            "SKIPPED",
            recovery_expectation,
            "The client did not reach the expected status.",
        )
    return status, retry, time


def demo_client(session: httpx.Client, *, attempts: int) -> int | None:
    """Exercise the same client logic with or without a retry budget."""
    status = None
    for _ in range(attempts):
        try:
            status = session.get(OPERATION.path_template).status_code
        except (httpx.ReadTimeout, httpx.ReadError):
            status = None
        if status not in (None, 429, 503):
            break
    return status
