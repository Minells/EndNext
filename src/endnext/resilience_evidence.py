"""Verify client measurements against a saved, correlated proxy trace."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .evidence import Assertion, Verdict
from .inputs import InputError, decode_json, read_file
from .resilience import Scenario, evaluate_outcome

TRACE_VERSION = "1"
_RUN_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_COMPLETED = {"complete", "client_finished"}
_INTERRUPTED = {"interrupted", "run_deadline_exhausted"}


def _status(value: object) -> int | None:
    if value is not None and (type(value) is not int or not 200 <= value <= 599):
        raise InputError(
            "Evidence status must be null or an HTTP status from 200 to 599."
        )
    return value


def _nonnegative(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise InputError(f"The {label} must be a non-negative integer.")
    return value


@dataclass(frozen=True)
class ClientObservation:
    run_id: str
    status: int | None
    elapsed_ms: int

    @property
    def digest(self) -> str:
        data = json.dumps(
            (self.run_id, self.status, self.elapsed_ms), separators=(",", ":")
        )
        return f"sha256:{hashlib.sha256(data.encode('ascii')).hexdigest()}"

    @classmethod
    def load(cls, path: Path) -> "ClientObservation":
        raw = decode_json(read_file(path, "client observation"), "client observation")
        run_id = raw.get("run_id")
        if (
            raw.keys() != {"run_id", "status", "elapsed_ms"}
            or not isinstance(run_id, str)
            or not _RUN_ID.fullmatch(run_id)
        ):
            raise InputError(
                "The client observation requires run_id, status, and elapsed_ms."
            )
        return cls(
            run_id,
            _status(raw["status"]),
            _nonnegative(raw["elapsed_ms"], "client elapsed_ms"),
        )


@dataclass(frozen=True)
class ProxyTrace:
    run_id: str
    attempts: int
    upstream_requests_sent: int | None
    observation_digest: str | None
    failed: bool

    @classmethod
    def load(cls, path: Path, scenario: Scenario) -> "ProxyTrace":
        lines = read_file(path, "proxy trace").splitlines()
        if not 1 <= len(lines) <= len(scenario.steps) + 2:
            raise InputError("The proxy trace contains an invalid number of events.")
        ready, *events = (decode_json(line, "proxy trace event") for line in lines)
        run_id = ready.get("run_id")
        if (
            ready.get("event") != "ready"
            or ready.get("trace_version") != TRACE_VERSION
            or not isinstance(run_id, str)
            or not _RUN_ID.fullmatch(run_id)
        ):
            raise InputError(
                "The proxy trace requires a supported, versioned ready event."
            )
        if (
            ready.get("case") != scenario.name
            or ready.get("scenario_digest") != f"sha256:{scenario.digest}"
            or ready.get("generation")
            != (
                asdict(scenario.generation) if scenario.generation is not None else None
            )
        ):
            raise InputError("The proxy trace does not match the supplied scenario.")
        if any(event.get("run_id") != run_id for event in events):
            raise InputError("The proxy trace mixes different runs.")

        summary = (
            events.pop() if events and events[-1].get("event") == "finished" else None
        )
        if len(events) > len(scenario.steps):
            raise InputError("The proxy trace contains more steps than the scenario.")
        elapsed_ms = 0
        failed = False
        for index, event in enumerate(events):
            step = scenario.steps[index]
            stamp = _nonnegative(event.get("elapsed_ms"), "proxy step elapsed_ms")
            if (
                event.get("event") != "step"
                or type(event.get("attempt")) is not int
                or event.get("attempt") != index + 1
                or type(event.get("outcome")) is not type(step.outcome)
                or event.get("outcome") != step.outcome
                or stamp < elapsed_ms
                or not {"status", "problem"} <= event.keys()
                or failed
            ):
                raise InputError("The proxy trace contains an invalid step sequence.")
            status = _status(event["status"])
            problem = event["problem"]
            if problem is None:
                if not _matches(step.outcome, status):
                    raise InputError("A proxy step status contradicts its outcome.")
            elif not isinstance(problem, str) or not problem:
                raise InputError("The proxy step contains invalid error metadata.")
            failed = problem is not None
            elapsed_ms = stamp

        if summary is None:
            return cls(run_id, len(events), None, None, failed)
        reason = summary.get("reason")
        if not isinstance(reason, str) or not reason:
            raise InputError("The proxy trace has an invalid completion reason.")
        attempted = _nonnegative(summary.get("attempts"), "proxy attempt count")
        sent = _nonnegative(
            summary.get("upstream_requests_sent"), "upstream request count"
        )
        completed = reason in _COMPLETED
        evidence = summary.get("client_observation_digest")
        forwards = sum(step.outcome == "forward" for step in scenario.steps[:attempted])
        if evidence is not None and (
            not isinstance(evidence, str)
            or not _DIGEST.fullmatch(evidence)
            or not completed
        ):
            raise InputError(
                "The proxy summary contains an invalid client observation digest."
            )
        if (
            "client_observation_digest" not in summary
            or (reason == "client_finished" and evidence is None)
            or not len(events) <= attempted <= len(events) + 1
            or (attempted > len(scenario.steps) and reason != "scenario_exhausted")
            or sent > forwards
            or (completed and (attempted != len(events) or sent != forwards or failed))
            or (reason == "complete" and attempted != len(scenario.steps))
        ):
            raise InputError("The proxy summary contradicts the recorded steps.")
        failed = failed or not (completed or reason in _INTERRUPTED)
        return cls(run_id, attempted, sent, evidence, failed)


def _matches(outcome: int | str, status: int | None) -> bool:
    if outcome == "forward":
        return status is not None
    if isinstance(outcome, int):
        return outcome == status
    return status is None


def assess_client(
    scenario: Scenario, trace: ProxyTrace, observation: ClientObservation
) -> tuple[Assertion, ...]:
    if trace.run_id != observation.run_id:
        raise InputError("The client observation belongs to a different proxy run.")
    if (
        trace.observation_digest is not None
        and trace.observation_digest != observation.digest
    ):
        raise InputError(
            "The client measurement does not match the observation recorded by the proxy."
        )
    expectation = "A completed proxy trace with observed dependency attempts."
    if trace.failed:
        verdict: Verdict = "ERROR"
        reason = "The proxy reported an execution error."
    elif trace.observation_digest is None:
        verdict = "INCONCLUSIVE"
        reason = "The proxy trace does not establish completion."
    elif not trace.attempts:
        verdict = "INCONCLUSIVE"
        reason = "No dependency attempts were observed."
    else:
        return evaluate_outcome(
            scenario,
            observation.status,
            trace.attempts,
            observation.elapsed_ms,
            timing_basis="client elapsed time",
        )
    return (Assertion("resilience.execution", verdict, expectation, reason),)
