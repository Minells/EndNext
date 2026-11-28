"""Compare the assertions in two local evidence reports."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from .coverage import Coverage
from .evidence import Assertion, Verdict
from .exceptions import FindingException
from .inputs import InputError
from .reporting import ReportCase, SavedReport, safe_terminal, summary_cell

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
    auth_profile: str = ""

    @classmethod
    def for_case(cls, case: ReportCase, rule: str) -> "CheckKey":
        return cls(
            case.operation.method.upper(),
            case.operation.path_template,
            case.name,
            case.source,
            rule,
            case.auth_profile or "",
        )

    def fingerprint(self, field: str | None) -> str:
        # Preserve the encoding used by saved comparisons, including profile omission.
        parts: tuple[str | None, ...] = (
            self.method,
            self.path_template,
            self.case,
            self.source,
            self.rule,
            field,
        )
        if self.auth_profile:
            parts += (self.auth_profile,)
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
    exception: FindingException | None = None

    @property
    def blocks(self) -> bool:
        if self.exception is not None:
            return self.exception.status == "expired"
        return self.kind == "new_failure"


@dataclass(frozen=True)
class BaselineDiff:
    baseline_checks: int
    current_checks: int
    compared_checks: int
    counts: dict[ChangeKind, int]
    changes: tuple[Change, ...]
    coverage: Coverage | None = None

    @property
    def blocking_regressions(self) -> bool:
        return any(change.blocks for change in self.changes)

    @property
    def incomplete_verification(self) -> bool:
        return (
            self.counts["unverified"] > 0
            or self.compared_checks == 0
            or (self.coverage is not None and self.coverage.incomplete > 0)
        )

    @property
    def exit_code(self) -> int:
        if self.blocking_regressions:
            return 1
        return 2 if self.incomplete_verification else 0


def _checks(report: SavedReport) -> dict[CheckKey, Assertion]:
    indexed = {}
    for case in report.cases:
        for assertion in case.assertions:
            key = CheckKey.for_case(case, assertion.rule)
            if key in indexed:
                raise InputError(
                    "A report contains duplicate assertions for the same case, operation, source, rule and profile."
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


def compare(
    baseline: SavedReport,
    current: SavedReport,
    *,
    exceptions: Mapping[str, FindingException] | None = None,
) -> BaselineDiff:
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
            observed = _state(key, after)
            exception = None
            if exceptions and observed is not None and observed.verdict == "FAIL":
                exception = exceptions.get(observed.fingerprint)
            changes.append(Change(key, kind, _state(key, before), observed, exception))
            totals[kind] += 1
    return BaselineDiff(
        len(left), len(right), compared, totals, tuple(changes), current.coverage
    )


def render_diff(result: BaselineDiff, *, output_format: str) -> str:
    if output_format == "json":
        payload = asdict(result)
        if result.coverage is None:
            del payload["coverage"]
        for change in payload["changes"]:
            if change["exception"] is None:
                del change["exception"]
        return json.dumps(
            {
                "schema_version": "1",
                "kind": "baseline_diff",
                "exit_code": result.exit_code,
                "blocking_regressions": result.blocking_regressions,
                "incomplete_verification": result.incomplete_verification,
                **payload,
            },
            indent=2,
        )
    lines = [
        f"{result.compared_checks} checks compared · "
        + " · ".join(
            f"{total} {kind.replace('_', ' ')}" for kind, total in result.counts.items()
        )
    ]
    if result.coverage is not None:
        lines.append(result.coverage.summary())
    if result.incomplete_verification:
        lines.append("Verification is incomplete.")
    for change in result.changes:
        key = change.check
        before = change.baseline.verdict if change.baseline else "missing"
        after = change.current.verdict if change.current else "missing"
        field = change.current.field if change.current else None
        if field is None and change.baseline:
            field = change.baseline.field
        location = f" at {field or '(root)'}" if field is not None else ""
        source = f"{key.source}, {key.auth_profile}" if key.auth_profile else key.source
        exception = ""
        if change.exception is not None:
            entry = change.exception
            exception = (
                f" · EXCEPTION {entry.status.upper()} (expiry {entry.expires_on} UTC)"
                f" · {entry.reason}"
            )
        lines.append(
            safe_terminal(
                f"{change.kind.replace('_', ' ').upper()} {key.method} {key.path_template} · "
                f"{key.case} [{source}] · {key.rule}{location} · {before} → {after}{exception}"
            )
        )
    if not result.compared_checks:
        lines.append("No checks have executed verdicts in both reports.")
    return "\n".join(lines)


_SUMMARY_LIMIT = 10


def render_summary(result: BaselineDiff) -> str:
    def flag(value: bool) -> str:
        return "yes" if value else "no"

    lines = [
        "### EndNext comparison",
        "",
        f"- Blocking regressions: **{flag(result.blocking_regressions)}**",
        f"- Incomplete verification: **{flag(result.incomplete_verification)}**",
        f"- Checks compared: {result.compared_checks}",
        *(
            f"- {kind.replace('_', ' ').capitalize()}: {total}"
            for kind, total in result.counts.items()
        ),
    ]
    if result.coverage is not None:
        lines.extend(("", result.coverage.summary()))
        missing = [item for item in result.coverage.operations if item.incomplete]
        if missing:
            lines.extend(
                ("", "#### Selected operations with incomplete verification", "")
            )
            for item in missing[:_SUMMARY_LIMIT]:
                operation = item.operation
                lines.append(
                    f"- {summary_cell(f'{operation.method} {operation.path_template}')}"
                )
            if len(missing) > _SUMMARY_LIMIT:
                lines.append(
                    f"- {len(missing) - _SUMMARY_LIMIT} more in the comparison artifact."
                )

    groups = (
        (
            "Blocking regressions",
            [change for change in result.changes if change.blocks],
        ),
        (
            "Unverified checks",
            [change for change in result.changes if change.kind == "unverified"],
        ),
    )
    for title, changes in groups:
        if not changes:
            continue
        lines.extend(
            (
                "",
                f"#### {title}",
                "",
                "| Operation | Case | Rule | Field |",
                "| --- | --- | --- | --- |",
            )
        )
        for change in changes[:_SUMMARY_LIMIT]:
            key = change.check
            state = (
                change.current if change.blocks else change.baseline or change.current
            )
            field = state.field if state is not None else None
            cells = (
                f"{key.method} {key.path_template}",
                key.case,
                key.rule,
                "(root)" if field == "" else field or "",
            )
            lines.append("| " + " | ".join(map(summary_cell, cells)) + " |")
        if len(changes) > _SUMMARY_LIMIT:
            lines.extend(
                (
                    "",
                    f"{len(changes) - _SUMMARY_LIMIT} more in the comparison artifact.",
                )
            )
    lines.extend(("", "See the comparison artifact for the full JSON evidence.", ""))
    return "\n".join(lines)
