"""Report data that contains no raw observation body or backend exceptions."""

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Literal
from uuid import uuid4


@dataclass(frozen=True)
class Operation:
    method: str
    path_template: str
    operation_id: str | None


type SecurityRequirements = list[dict[str, list[str]]]


@dataclass(frozen=True)
class InventoryOperation:
    operation: Operation
    request_media_types: list[str]
    response_media_types: dict[str, list[str]]
    declared_security: SecurityRequirements | None


@dataclass(frozen=True)
class Inventory:
    declared_security: SecurityRequirements | None
    operations: list[InventoryOperation]


type Verdict = Literal["PASS", "FAIL", "WARN", "SKIPPED", "INCONCLUSIVE", "ERROR"]
VERDICTS: tuple[Verdict, ...] = (
    "ERROR",
    "INCONCLUSIVE",
    "FAIL",
    "WARN",
    "PASS",
    "SKIPPED",
)


@dataclass(frozen=True)
class Assertion:
    rule: str
    verdict: Verdict
    expectation: str
    reason: str
    field: str | None = None
    observed_type: str | None = None


def counts(assertions: Iterable[Assertion]) -> dict[str, int]:
    result: dict[str, int] = dict.fromkeys(VERDICTS, 0)
    for assertion in assertions:
        result[assertion.verdict] += 1
    return result


def primary(assertions: tuple[Assertion, ...]) -> Assertion:
    return min(assertions, key=lambda assertion: VERDICTS.index(assertion.verdict))


def report(
    assertion: Assertion,
    operation: Operation,
    *,
    engine_version: str,
    contract_digest: str,
    case: str,
    fixture: str,
    status: int,
    media_type: str | None,
    assertions: tuple[Assertion, ...],
) -> dict[str, object]:
    return {
        "schema_version": "2",
        "engine_version": engine_version,
        "run_id": str(uuid4()),
        "contract_digest": f"sha256:{contract_digest}",
        "case": case,
        "operation": asdict(operation),
        "source": {"kind": "imported_fixture", "fixture": fixture},
        "observation": {"status": status, "media_type": media_type},
        **asdict(assertion),
        "assertions": [asdict(item) for item in assertions],
        "counts": counts(assertions),
    }
