"""Operation coverage carried as optional v2 report metadata."""

from dataclasses import dataclass

from .evidence import Operation
from .inputs import InputError, Json


@dataclass(frozen=True)
class OperationCoverage:
    operation: Operation
    selected: bool
    executed: bool
    incomplete: bool


@dataclass(frozen=True)
class Coverage:
    operations: tuple[OperationCoverage, ...]

    @property
    def incomplete(self) -> int:
        return sum(item.incomplete for item in self.operations)

    def summary(self) -> str:
        selected = sum(item.selected for item in self.operations)
        executed = sum(item.executed for item in self.operations)
        return (
            f"{len(self.operations)} contract operations · {selected} selected · "
            f"{executed}/{selected} selected operations with executed checks · "
            f"{self.incomplete}/{selected} selected operations with incomplete verification"
        )

    @classmethod
    def from_document(cls, data: Json) -> "Coverage":
        error = InputError("The report contains invalid operation coverage.")
        if not isinstance(data, dict) or not isinstance(data.get("operations"), list):
            raise error
        entries = data["operations"]
        assert isinstance(entries, list)
        operations = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("operation"), dict
            ):
                raise error
            operation = entry["operation"]
            assert isinstance(operation, dict)
            method, path = operation.get("method"), operation.get("path_template")
            identifier = operation.get("operation_id")
            selected = entry.get("selected")
            executed = entry.get("executed")
            incomplete = entry.get("incomplete")
            if (
                not isinstance(method, str)
                or not isinstance(path, str)
                or (identifier is not None and not isinstance(identifier, str))
                or not isinstance(selected, bool)
                or not isinstance(executed, bool)
                or not isinstance(incomplete, bool)
                or (not selected and (executed or incomplete))
                or (selected and not executed and not incomplete)
            ):
                raise error
            key = (method.upper(), path)
            if key in seen:
                raise error
            seen.add(key)
            operations.append(
                OperationCoverage(
                    Operation(method, path, identifier), selected, executed, incomplete
                )
            )
        return cls(tuple(operations))
