"""Terminal entry point."""

import argparse
import json
import sys
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn

from .contract import Contract
from .evidence import Inventory, Operation, report
from .inputs import Fixture, InputError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default error message echoes user-supplied arguments.
        self.print_usage(sys.stderr)
        self.exit(2, "ERROR [input] Invalid arguments. See --help.\n")


def main(argv: list[str] | None = None) -> int:
    engine_version = version("endnext")
    parser = _Parser(
        prog="endnext", description="Verify imported API responses offline."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {engine_version}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Check one local JSON response fixture.")
    inspect = commands.add_parser(
        "inspect", help="List the operations in a local contract."
    )
    for command in (check, inspect):
        command.add_argument(
            "contract", type=Path, help="Local OpenAPI 3.1 JSON or YAML document"
        )
        command.add_argument(
            "--format", choices=("text", "json"), default="text", help="Output format"
        )
    check.add_argument("fixture", type=Path, help="Local JSON response fixture")
    args = parser.parse_args(argv)

    try:
        contract = Contract.load(args.contract)
        if args.command == "inspect":
            _show_inventory(contract.inventory(), args.format)
            return 0
        return _show_check(
            contract, Fixture.load(args.fixture), engine_version, args.format
        )
    except InputError as error:
        print(f"ERROR [input] {error}", file=sys.stderr)
        return 2
    except Exception:
        # Validator exceptions can include the entire response.
        print(
            "ERROR [execution] EndNext could not complete the command.", file=sys.stderr
        )
        return 2


def _show_check(
    contract: Contract, fixture: Fixture, engine_version: str, output_format: str
) -> int:
    operation, assertion = contract.check(fixture)
    if output_format == "json":
        result = report(
            assertion,
            operation,
            engine_version=engine_version,
            contract_digest=contract.digest,
            case=fixture.case,
            fixture=fixture.filename,
            status=fixture.status,
        )
        print(json.dumps(result, indent=2))
    else:
        field = "" if assertion.field is None else f" at {assertion.field or '(root)'}"
        print(
            f"{assertion.verdict} {_operation_name(operation)} · {assertion.rule}{_terminal(field)}"
        )
        print(
            f"{assertion.reason} Observation: imported fixture, HTTP {fixture.status}."
        )
    return int(assertion.verdict == "FAIL")


def _show_inventory(inventory: Inventory, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(asdict(inventory), indent=2))
        return
    if inventory.declared_security is not None:
        print("Contract security " + json.dumps(inventory.declared_security))
    if not inventory.operations:
        print("No operations declared in paths.")
    for entry in inventory.operations:
        print(_operation_name(entry.operation))
        if entry.request_media_types:
            print("  Request " + _terminal(", ".join(entry.request_media_types)))
        for status, media_types in entry.response_media_types.items():
            print(
                "  Response " + _terminal(f"{status} {', '.join(media_types)}".rstrip())
            )
        if entry.declared_security is not None:
            print("  Operation security " + json.dumps(entry.declared_security))


def _operation_name(operation: Operation) -> str:
    name = f"{operation.method} {operation.path_template}"
    if operation.operation_id:
        name += f" ({operation.operation_id})"
    return _terminal(name)


def _terminal(value: str) -> str:
    return "".join(
        char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
        for char in value
    )
