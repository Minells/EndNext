"""Render saved EndNext evidence with bounded, escaped presentation."""

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .evidence import VERDICTS, Assertion, Operation, Verdict, counts
from .inputs import InputError, Json, JsonObject, decode_json, read_file, walk


@dataclass(frozen=True)
class ReportCase:
    name: str
    operation: Operation
    source: str
    assertions: tuple[Assertion, ...]
    fixture: str | None = None
    auth_profile: str | None = None


@dataclass(frozen=True)
class SavedReport:
    cases: tuple[ReportCase, ...]
    counts: dict[str, int]
    requests_sent: int | None

    @classmethod
    def load(cls, path: Path) -> "SavedReport":
        obj = decode_json(read_file(path, "report"), "report")
        for _ in walk(obj, "report"):
            pass
        version = obj.get("schema_version")
        if version not in ("1", "2"):
            raise InputError("Unsupported EndNext report schema version.")
        cases = obj.get("cases")
        entries: tuple[ReportCase, ...]
        if cases is None:
            entries = (_case(obj, version),)
        elif isinstance(cases, list):
            entries = tuple(_case(_object(case), version) for case in cases)
        else:
            raise InputError("The report contains invalid cases.")
        actual = counts(assertion for case in entries for assertion in case.assertions)
        if version == "2":
            declared = obj.get("counts")
            if (
                not isinstance(declared, dict)
                or declared != actual
                or any(type(value) is not int for value in declared.values())
            ):
                raise InputError("The saved report counts do not match its assertions.")
        sent = obj.get("requests_sent")
        if sent is not None and (type(sent) is not int or sent < 0):
            raise InputError("The report contains an invalid request count.")
        return cls(entries, actual, sent)


def _object(value: Json) -> JsonObject:
    if not isinstance(value, dict):
        raise InputError("The report contains an invalid record.")
    return value


def _text(value: Json) -> str:
    if not isinstance(value, str):
        raise InputError("The report contains invalid text metadata.")
    return value


def _assertion(obj: JsonObject) -> Assertion:
    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        raise InputError("The report contains an unknown verdict.")
    field = obj.get("field")
    return Assertion(
        _text(obj.get("rule")),
        verdict,
        _text(obj.get("expectation")),
        _text(obj.get("reason")),
        _text(field) if field is not None else None,
    )


def _case(obj: JsonObject, version: Json) -> ReportCase:
    operation = _object(obj.get("operation"))
    raw_source = obj.get("source")
    fixture = None
    if isinstance(raw_source, dict):
        source = _text(raw_source.get("kind"))
        if raw_source.get("fixture") is not None:
            fixture = _text(raw_source.get("fixture"))
    else:
        source = _text(raw_source)
    identifier = operation.get("operation_id")
    profile = obj.get("auth_profile")
    raw_assertions = obj.get("assertions")
    if isinstance(raw_assertions, list):
        assertions = tuple(_assertion(_object(item)) for item in raw_assertions)
    elif version == "1" and "verdict" in obj:
        assertions = (_assertion(obj),)
    elif version == "1" and isinstance(obj.get("assertion"), dict):
        assertions = (_assertion(_object(obj.get("assertion"))),)
    elif version == "1" and obj.get("state") in VERDICTS:
        assertions = (
            Assertion(
                "case.outcome",
                cast(Verdict, obj.get("state")),
                "",
                _text(obj.get("reason")),
            ),
        )
    else:
        raise InputError("The report contains invalid assertions.")
    return ReportCase(
        _text(obj.get("case")),
        Operation(
            _text(operation.get("method")),
            _text(operation.get("path_template")),
            _text(identifier) if identifier is not None else None,
        ),
        source,
        assertions,
        fixture,
        _text(profile) if profile is not None else None,
    )


def safe_terminal(value: str) -> str:
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in value
    )


_MARKDOWN = re.compile(r"([\\`*_{}\[\]()#+\-!|~])")


def _cell(value: str) -> str:
    return _MARKDOWN.sub(r"\\\1", html.escape(safe_terminal(value)))


def render(saved: SavedReport, *, markdown: bool) -> str:
    summary = format_counts(saved.counts)
    if saved.requests_sent is not None:
        summary += f" · {saved.requests_sent} requests sent"
    lines = [summary, ""]
    if markdown:
        lines.extend(
            (
                "| Case | Operation | Source | Verdict | Rule | Location | Reason |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            )
        )
    for case in saved.cases:
        operation = f"{case.operation.method} {case.operation.path_template}"
        if case.operation.operation_id:
            operation += f" ({case.operation.operation_id})"
        source = (
            f"{case.source} ({case.fixture})"
            if case.fixture is not None
            else case.source
        )
        if case.auth_profile is not None:
            source += f" ({case.auth_profile})"
        for assertion in case.assertions:
            values = (
                case.name,
                operation,
                source,
                assertion.verdict,
                assertion.rule,
                assertion.field or "",
                assertion.reason,
            )
            if markdown:
                lines.append("| " + " | ".join(map(_cell, values)) + " |")
            else:
                lines.append(" · ".join(map(safe_terminal, values)))
    return "\n".join(lines).rstrip()


def format_counts(totals: dict[str, int]) -> str:
    return " · ".join(f"{verdict} {totals[verdict]}" for verdict in VERDICTS)
