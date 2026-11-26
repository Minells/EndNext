"""Terminal entry point."""

import argparse
import json
import sys
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn, Protocol
from uuid import uuid4

from .baseline import compare, render_diff
from .branding import help_heading
from .contract import Contract
from .evidence import Inventory, Operation, counts, primary, report
from .inputs import Fixture, InputError
from .planner import Plan, PlanConfig, build_plan
from .reporting import SavedReport, format_counts, render
from .reporting import safe_terminal as _terminal
from .rules import verify
from .scan import ScanRun, scan
from .transport import Target


class _Writable(Protocol):
    def write(self, value: str, /) -> object: ...


class _Parser(argparse.ArgumentParser):
    def print_help(self, file: _Writable | None = None) -> None:
        output = sys.stdout if file is None else file
        output.write(f"{help_heading(output)}\n\n")
        super().print_help(output)

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
    plan = commands.add_parser(
        "plan", help="Plan local cases and estimate their request cost."
    )
    scan_command = commands.add_parser(
        "scan", help="Check explicitly authorized loopback GET cases."
    )
    report_command = commands.add_parser(
        "report", help="Render a saved JSON evidence report."
    )
    report_command.add_argument(
        "input_report", type=Path, help="Saved EndNext check or scan JSON"
    )
    report_command.add_argument(
        "--format", choices=("text", "markdown"), default="text"
    )
    diff_command = commands.add_parser(
        "diff", help="Compare a saved baseline with the current evidence."
    )
    diff_command.add_argument(
        "baseline", type=Path, help="Saved baseline check or scan JSON"
    )
    diff_command.add_argument(
        "current", type=Path, help="Saved current check or scan JSON"
    )
    diff_command.add_argument("--format", choices=("text", "json"), default="text")
    for command in (check, inspect, plan, scan_command):
        command.add_argument(
            "contract", type=Path, help="Local OpenAPI 3.1 JSON or YAML document"
        )
        command.add_argument(
            "--format", choices=("text", "json"), default="text", help="Output format"
        )
    check.add_argument("fixture", type=Path, help="Local JSON response fixture")
    for command in (plan, scan_command):
        command.add_argument("config", type=Path, help="Local TOML plan configuration")
    args = parser.parse_args(argv)

    try:
        if args.command == "diff":
            delta = compare(
                SavedReport.load(args.baseline), SavedReport.load(args.current)
            )
            print(render_diff(delta, output_format=args.format))
            return delta.exit_code
        if args.command == "report":
            print(
                render(
                    SavedReport.load(args.input_report),
                    markdown=args.format == "markdown",
                )
            )
            return 0
        contract = Contract.load(args.contract)
        if args.command == "inspect":
            _show_inventory(contract.inventory(), args.format)
            return 0
        if args.command == "plan":
            config = PlanConfig.load(args.config)
            target = Target.load(config.target) if config.target is not None else None
            result = build_plan(
                contract.inventory(),
                config,
                allows_http=(lambda case: target.request(case) is not None)
                if target is not None
                else None,
            )
            _show_plan(result, args.format)
            return 0
        if args.command == "scan":
            run = scan(contract, PlanConfig.load(args.config))
            _show_scan(run, contract.digest, engine_version, args.format)
            return run.exit_code
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
    operation, assertions = verify(contract, fixture, imported=True)
    assertion = primary(assertions)
    if output_format == "json":
        result = report(
            assertion,
            operation,
            engine_version=engine_version,
            contract_digest=contract.digest,
            case=fixture.case,
            fixture=fixture.filename,
            status=fixture.status,
            media_type=fixture.media_type,
            assertions=assertions,
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
        for detail in assertions:
            if detail is not assertion:
                print(f"  {detail.verdict} {detail.rule} · {detail.reason}")
        print(format_counts(counts(assertions)))
    if assertion.verdict in {"ERROR", "INCONCLUSIVE"}:
        return 2
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


def _show_plan(plan: Plan, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps({"schema_version": "1", **asdict(plan)}, indent=2))
        return
    for entry in plan.operations:
        selection = "selected" if entry.selected else "excluded"
        access = (
            "anonymous allowed" if entry.allows_anonymous else "authentication required"
        )
        print(f"{_operation_name(entry.operation)} ({selection}, {access})")
        if not entry.cases:
            print("  No cases configured.")
        for case in entry.cases:
            if case.reason is not None:
                print(
                    f"  SKIPPED {case.name} [{case.source}] · {case.reason.replace('_', ' ')}"
                )
            else:
                print(
                    f"  PLANNED {case.name} [{case.source}] · "
                    f"{case.request_cost} requests, {len(case.candidate_rules)} candidate assertions"
                )
                for rule in case.candidate_rules:
                    print(f"    {rule.rule} · {rule.condition}")
    print(
        f"{len(plan.operations)} operations · {plan.cases_planned} planned cases · "
        f"{plan.cases_skipped} skipped · {plan.requests_planned}/{plan.request_budget} requests · "
        f"{plan.candidate_assertions} candidate assertions"
    )


def _show_scan(
    run: ScanRun, digest: str, engine_version: str, output_format: str
) -> None:
    totals = counts(assertion for case in run.cases for assertion in case.assertions)
    if output_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": "2",
                    "engine_version": engine_version,
                    "run_id": str(uuid4()),
                    "contract_digest": f"sha256:{digest}",
                    **asdict(run),
                    "counts": totals,
                },
                indent=2,
            )
        )
        return
    for case in run.cases:
        print(
            f"{case.state} {_operation_name(case.operation)} · {case.case} · {case.reason}"
        )
        for assertion in case.assertions:
            print(f"  {assertion.verdict} {assertion.rule} · {assertion.reason}")
    print(
        f"{run.requests_sent} requests sent · "
        f"{run.requests_attempted}/{run.request_budget} attempts"
    )
    print(format_counts(totals))
