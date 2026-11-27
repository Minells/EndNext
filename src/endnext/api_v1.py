"""Version 1 of the offline fixture verification API."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from .contract import Contract
from .evidence import Assertion, Operation, Verdict, primary, report
from .inputs import Fixture, InputError
from .rules import verify

__all__ = [
    "Assertion",
    "CheckResult",
    "ExecutionError",
    "FixtureChecker",
    "InputError",
    "Operation",
    "Verdict",
]


class ExecutionError(Exception):
    """An unexpected failure with a message safe to log."""


@contextmanager
def _safe_errors() -> Iterator[None]:
    try:
        yield
    except InputError:
        raise
    except Exception:
        raise ExecutionError(
            "EndNext could not complete fixture verification."
        ) from None


@dataclass(frozen=True)
class CheckResult:
    operation: Operation
    assertions: tuple[Assertion, ...]
    case: str
    fixture: str
    status: int
    media_type: str | None
    contract_digest: str

    @property
    def assertion(self) -> Assertion:
        return primary(self.assertions)

    @property
    def exit_code(self) -> int:
        verdict = self.assertion.verdict
        if verdict in {"ERROR", "INCONCLUSIVE"}:
            return 2
        return int(verdict == "FAIL")

    def to_report(self) -> dict[str, object]:
        """Produce a v2 evidence report with a fresh run ID."""
        return report(
            self.assertion,
            self.operation,
            engine_version=version("endnext"),
            contract_digest=self.contract_digest,
            case=self.case,
            fixture=self.fixture,
            status=self.status,
            media_type=self.media_type,
            assertions=self.assertions,
        )


class FixtureChecker:
    """Validate one local contract and reuse it for sequential fixture checks."""

    def __init__(self, contract: str | Path):
        with _safe_errors():
            self._contract = Contract.load(Path(contract))

    def check(self, fixture: str | Path) -> CheckResult:
        with _safe_errors():
            loaded = Fixture.load(Path(fixture))
            operation, assertions = verify(self._contract, loaded, imported=True)
        return CheckResult(
            operation,
            assertions,
            loaded.case,
            loaded.filename,
            loaded.status,
            loaded.media_type,
            self._contract.digest,
        )
