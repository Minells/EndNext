"""Join scan findings and resilience assertions from the same measured request."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .baseline import CheckKey
from .evidence import Operation, Verdict, primary
from .inputs import InputError, decode_json, read_file
from .reporting import SavedReport, safe_terminal
from .resilience_evidence import (
    ClientObservation,
    ProxyReference,
    evidence_digest,
    evidence_run_id,
)


@dataclass(frozen=True)
class Finding:
    rule: str
    verdict: Verdict
    field: str | None
    fingerprint: str


@dataclass(frozen=True)
class RelatedAssertion:
    rule: str
    verdict: Verdict


@dataclass(frozen=True)
class Correlation:
    scan_run_id: str
    proxy_run_id: str
    scenario_digest: str
    client_observation_digest: str
    case: str
    operation: Operation
    auth_profile: str | None
    scan_state: Verdict
    findings: tuple[Finding, ...]
    resilience_assertions: tuple[RelatedAssertion, ...]


def correlate(scan_path: Path, resilience_path: Path) -> Correlation:
    scan_document = decode_json(read_file(scan_path, "scan report"), "scan report")
    resilience_document = decode_json(
        read_file(resilience_path, "resilience report"), "resilience report"
    )
    scan = SavedReport.from_document(scan_document)
    resilience = SavedReport.from_document(resilience_document)
    link = scan_document.get("proxy_observation")
    requests_attempted = scan_document.get("requests_attempted")
    if (
        scan_document.get("schema_version") != "2"
        or not isinstance(link, dict)
        or type(requests_attempted) is not int
        or requests_attempted != 1
        or scan.requests_sent != 1
    ):
        raise InputError(
            "Correlation requires a scan report with one measured proxy observation."
        )
    proxy = ProxyReference(
        evidence_run_id(link.get("run_id")),
        evidence_digest(link.get("scenario_digest")),
    )
    observation_digest = evidence_digest(link.get("client_observation_digest"))
    related = ProxyReference(
        evidence_run_id(resilience_document.get("run_id")),
        evidence_digest(resilience_document.get("scenario_digest")),
    )
    if proxy != related or observation_digest != resilience_document.get(
        "client_observation_digest"
    ):
        raise InputError(
            "The reports do not establish the same proxy run and client measurement."
        )
    if "final_status" not in resilience_document:
        raise InputError("The resilience report is missing the final client status.")
    observation = ClientObservation.from_document(
        {
            "run_id": related.run_id,
            "status": resilience_document.get("final_status"),
            "elapsed_ms": resilience_document.get("client_elapsed_ms"),
        }
    )
    if observation.digest != observation_digest:
        raise InputError("The resilience measurement contradicts its recorded digest.")

    cases = [
        case
        for case in scan.cases
        if case.name == link.get("case") and case.source == "live_http"
    ]
    if len(cases) != 1 or not cases[0].assertions:
        raise InputError(
            "The scan report does not identify a unique live case for the observation."
        )
    (case,) = cases
    dependency_attempts = resilience_document.get("dependency_attempts")
    if (
        resilience_document.get("schema_version") != "2"
        or type(dependency_attempts) is not int
        or dependency_attempts < 1
        or len(resilience.cases) != 1
        or resilience.cases[0].source != "live_proxy"
        or not resilience.cases[0].assertions
        or any(
            assertion.verdict in {"ERROR", "INCONCLUSIVE"}
            for assertion in resilience.cases[0].assertions
        )
    ):
        raise InputError("Correlation requires a completed live resilience assessment.")

    findings = tuple(
        Finding(
            assertion.rule,
            assertion.verdict,
            assertion.field,
            CheckKey.for_case(case, assertion.rule).fingerprint(assertion.field),
        )
        for assertion in case.assertions
        if assertion.verdict in {"FAIL", "WARN"}
    )
    return Correlation(
        evidence_run_id(scan_document.get("run_id")),
        proxy.run_id,
        proxy.scenario_digest,
        observation_digest,
        case.name,
        case.operation,
        case.auth_profile,
        primary(case.assertions).verdict,
        findings,
        tuple(
            RelatedAssertion(assertion.rule, assertion.verdict)
            for assertion in resilience.cases[0].assertions
        ),
    )


def render_correlation(result: Correlation, *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(
            {
                "schema_version": "1",
                "kind": "scan_resilience_correlation",
                **asdict(result),
            },
            indent=2,
        )
    lines = [
        safe_terminal(
            f"{result.operation.method} {result.operation.path_template} · {result.case}"
        ),
        f"Scan {result.scan_run_id} ({result.scan_state}) · proxy {result.proxy_run_id}",
        f"Scenario {result.scenario_digest}",
        f"{len(result.findings)} scan findings in this proxy run",
    ]
    for finding in result.findings:
        lines.append(
            safe_terminal(f"  {finding.verdict} {finding.rule} · {finding.fingerprint}")
        )
    lines.append("Resilience assertions")
    lines.extend(
        safe_terminal(f"  {assertion.verdict} {assertion.rule}")
        for assertion in result.resilience_assertions
    )
    return "\n".join(lines)
