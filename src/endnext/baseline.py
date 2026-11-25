"""Compare the assertions in two local evidence reports."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from .evidence import Assertion, Verdict
from .inputs import InputError
from .reporting import SavedReport, safe_terminal

type ChangeKind = Literal[
    "new_failure",
    "existing_failure",
    "resolved_failure",
    "new_warning",
    "existing_warning",
    "resolved_warning",
    "unverified",
]

_CHANGE_KINDS: tuple[ChangeKind, ...] = (
    "new_failure",
    "existing_failure",
    "resolved_failure",
    "new_warning",
    "existing_warning",
    "resolved_warning",
    "unverified",
)
_EXECUTED = {"PASS", "FAIL", "WARN"}


@dataclass(frozen=True, order=True)
class CheckKey:
    method: str
    path_template: str
    case: str
    source: str
    rule: str

    def fingerprint(self, field: str | None) -> str:
        parts = (
            self.method,
            self.path_template,
            self.case,
            self.source,
            self.rule,
            field,
        )
        encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode(
            "ascii"
        )
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class AssertionState:
    verdict: Verdict
    field: str | None
    fingerprint: str


@dataclass(frozen=True)
class Change:
    check: CheckKey
    kind: ChangeKind
    baseline: AssertionState | None
    current: AssertionState | None


@dataclass(frozen=True)
class BaselineDiff:
    baseline_checks: int
    current_checks: int
    compared_checks: int
    counts: dict[ChangeKind, int]
    changes: tuple[Change, ...]

    @property
    def exit_code(self) -> int:
        if self.counts["new_failure"]:
            return 1
        if self.counts["unverified"] or self.compared_checks == 0:
            return 2
        return 0


def _checks(report: SavedReport) -> dict[CheckKey, Assertion]:
    indexed = {}
    for case in report.cases:
        for assertion in case.assertions:
            key = CheckKey(
                case.operation.method.upper(),
                case.operation.path_template,
                case.name,
                case.source,
                assertion.rule,
            )
            if key in indexed:
                raise InputError(
                    "A report contains duplicate assertions for the same case, operation, source and rule."
                )
            indexed[key] = assertion
    return indexed


def _state(key: CheckKey, assertion: Assertion | None) -> AssertionState | None:
    if assertion is None:
        return None
    return AssertionState(
        assertion.verdict, assertion.field, key.fingerprint(assertion.field)
    )


def _classify(before: Assertion | None, after: Assertion | None) -> ChangeKind | None:
    baseline = before.verdict if before else None
    current = after.verdict if after else None
    same_location = (
        before is not None and after is not None and before.field == after.field
    )
    if current == "FAIL":
        return (
            "existing_failure"
            if baseline == "FAIL" and same_location
            else "new_failure"
        )
    if current in {"ERROR", "INCONCLUSIVE"} or (
        baseline in _EXECUTED and current not in _EXECUTED
    ):
        return "unverified"
    if baseline == "FAIL":
        return "resolved_failure" if current == "PASS" else "unverified"
    if current == "WARN":
        return (
            "existing_warning"
            if baseline == "WARN" and same_location
            else "new_warning"
        )
    if baseline == "WARN" and current == "PASS":
        return "resolved_warning"
    return None


def compare(baseline: SavedReport, current: SavedReport) -> BaselineDiff:
    left = _checks(baseline)
    right = _checks(current)
    changes = []
    totals = dict.fromkeys(_CHANGE_KINDS, 0)
    compared = 0
    for key in sorted(left.keys() | right.keys()):
        before = left.get(key)
        after = right.get(key)
        if (
            before
            and after
            and before.verdict in _EXECUTED
            and after.verdict in _EXECUTED
        ):
            compared += 1
        kind = _classify(before, after)
        if kind is not None:
            changes.append(Change(key, kind, _state(key, before), _state(key, after)))
            totals[kind] += 1
    return BaselineDiff(len(left), len(right), compared, totals, tuple(changes))


def render_diff(result: BaselineDiff, *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(
            {
                "schema_version": "1",
                "kind": "baseline_diff",
                "exit_code": result.exit_code,
                **asdict(result),
            },
            indent=2,
        )
    lines = [
        f"{result.compared_checks} checks compared · "
        + " · ".join(
            f"{total} {kind.replace('_', ' ')}" for kind, total in result.counts.items()
        )
    ]
    for change in result.changes:
        key = change.check
        before = change.baseline.verdict if change.baseline else "missing"
        after = change.current.verdict if change.current else "missing"
        field = change.current.field if change.current else None
        if field is None and change.baseline:
            field = change.baseline.field
        location = f" at {field or '(root)'}" if field is not None else ""
        lines.append(
            safe_terminal(
                f"{change.kind.replace('_', ' ').upper()} {key.method} {key.path_template} · "
                f"{key.case} [{key.source}] · {key.rule}{location} · {before} → {after}"
            )
        )
    if not result.compared_checks:
        lines.append("No checks have executed verdicts in both reports.")
    return "\n".join(lines)
