"""Terminal entry point."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import NoReturn, Protocol
from uuid import uuid4

from .api_v1 import CheckResult, FixtureChecker
from .baseline import compare, render_diff
from .branding import help_heading
from .contract import Contract
from .correlation import correlate, render_correlation
from .evidence import Assertion, Inventory, Operation, counts
from .exceptions import load_exceptions
from .inputs import InputError
from .planner import Plan, PlanConfig, build_plan
from .policy import Target
from .reporting import SavedReport, format_counts, render
from .reporting import safe_terminal as _terminal
from .resilience import OPERATION as DEPENDENCY_OPERATION
from .resilience import Scenario, demo_client, resilience_exit_code, run_scenario
from .resilience_evidence import (
    ClientObservation,
    ProxyReference,
    ProxyTrace,
    assess_client,
)
from .resilience_proxy import serve_proxy
from .resilience_scenario import materialize_scenario
from .scan import ScanRun, scan


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
    scan_command.add_argument(
        "--allow-bearer",
        nargs=3,
        action="append",
        default=[],
        metavar=("PROFILE", "ENV", "ORIGIN"),
        help="Authorize this credential environment variable for a profile and exact origin",
    )
    scan_command.add_argument(
        "--proxy-trace", type=Path, help="A proxy trace containing its ready event"
    )
    scan_command.add_argument(
        "--proxy-observation",
        type=Path,
        help="Write the measured client observation for the proxy",
    )
    correlate_command = commands.add_parser(
        "correlate",
        help="Join scan findings with a completed proxy assessment offline.",
    )
    correlate_command.add_argument("scan_report", type=Path)
    correlate_command.add_argument("resilience_report", type=Path)
    correlate_command.add_argument("--format", choices=("text", "json"), default="text")
    resilience = commands.add_parser(
        "resilience", help="Exercise a demo client with a local dependency scenario."
    )
    resilience.add_argument(
        "scenario", type=Path, help="Local TOML resilience scenario"
    )
    resilience.add_argument("--client", choices=("retry", "single"), default="retry")
    resilience.add_argument("--format", choices=("text", "json"), default="text")
    generate = commands.add_parser(
        "resilience-generate", help="Materialize a seeded resilience template as TOML."
    )
    generate.add_argument("template", type=Path, help="Local TOML resilience template")
    generate.add_argument(
        "--seed", type=int, required=True, help="Seed from 0 to 2^63 - 1"
    )
    proxy = commands.add_parser(
        "resilience-proxy", help="Serve a bounded HTTP/1.1 scenario on loopback."
    )
    proxy.add_argument("scenario", type=Path, help="Local TOML resilience scenario")
    proxy.add_argument(
        "--upstream",
        required=True,
        help="Authorized loopback origin with explicit port",
    )
    proxy.add_argument(
        "--authorized",
        action="store_true",
        help="Declare authorization for the upstream dependency",
    )
    proxy.add_argument(
        "--port",
        type=int,
        default=8766,
        help="Local listener port, or 0 for a free port",
    )
    proxy.add_argument(
        "--observation", type=Path, help="Close after observing completion for this run"
    )
    live_check = commands.add_parser(
        "resilience-check",
        help="Verify a saved proxy trace and real client observation offline.",
    )
    live_check.add_argument(
        "scenario", type=Path, help="Local TOML resilience scenario"
    )
    live_check.add_argument("trace", type=Path, help="Saved proxy JSON Lines trace")
    live_check.add_argument(
        "observation", type=Path, help="Saved client measurement JSON"
    )
    live_check.add_argument("--format", choices=("text", "json"), default="text")
    report_command = commands.add_parser(
        "report", help="Render a saved JSON evidence report."
    )
    report_command.add_argument(
        "input_report", type=Path, help="Saved EndNext evidence JSON"
    )
    report_command.add_argument(
        "--format", choices=("text", "markdown"), default="text"
    )
    diff_command = commands.add_parser(
        "diff", help="Compare a saved baseline with the current evidence."
    )
    diff_command.add_argument(
        "baseline", type=Path, help="Saved baseline evidence JSON"
    )
    diff_command.add_argument("current", type=Path, help="Saved current evidence JSON")
    diff_command.add_argument("--format", choices=("text", "json"), default="text")
    diff_command.add_argument(
        "--exceptions", type=Path, help="Local TOML finding exceptions"
    )
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
        if args.command == "correlate":
            joined = correlate(args.scan_report, args.resilience_report)
            print(render_correlation(joined, output_format=args.format))
            return 0
        if args.command == "diff":
            exceptions = (
                load_exceptions(args.exceptions, today=datetime.now(UTC).date())
                if args.exceptions is not None
                else None
            )
            delta = compare(
                SavedReport.load(args.baseline),
                SavedReport.load(args.current),
                exceptions=exceptions,
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
        if args.command == "resilience-generate":
            print(materialize_scenario(args.template, args.seed), end="")
            return 0
        if args.command == "resilience":
            return _show_resilience(
                Scenario.load(args.scenario), args.client, engine_version, args.format
            )
        if args.command == "resilience-proxy":
            return serve_proxy(
                Scenario.load(args.scenario, allow_forward=True),
                upstream=args.upstream,
                authorized=args.authorized,
                port=args.port,
                emit=lambda event: print(json.dumps(event), flush=True),
                observation=args.observation,
            )
        if args.command == "resilience-check":
            scenario = Scenario.load(args.scenario, allow_forward=True)
            return _show_resilience_check(
                scenario,
                ProxyTrace.load(args.trace, scenario),
                ClientObservation.load(args.observation),
                engine_version,
                args.format,
            )
        if args.command == "check":
            return _show_check(
                FixtureChecker(args.contract).check(args.fixture), args.format
            )
        contract = Contract.load(args.contract)
        if args.command == "inspect":
            _show_inventory(contract.inventory(), args.format)
            return 0
        config = PlanConfig.load(args.config)
        if args.command == "plan":
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
        proxy_reference = _scan_proxy_reference(
            args.proxy_trace,
            args.proxy_observation,
            args.contract,
            args.config,
            *(case.fixture for case in config.cases if case.fixture is not None),
        )
        run = scan(
            contract,
            config,
            bearer_grants=frozenset(tuple(grant) for grant in args.allow_bearer),
            proxy_run_id=proxy_reference.run_id
            if proxy_reference is not None
            else None,
        )
        if run.measurement is not None:
            run.measurement.observation.save(args.proxy_observation)
        _show_scan(
            run, contract.digest, engine_version, args.format, proxy=proxy_reference
        )
        return run.exit_code
    except InputError as error:
        print(f"ERROR [input] {error}", file=sys.stderr)
        return 2
    except Exception:
        # Validator exceptions can include the entire response.
        print(
            "ERROR [execution] EndNext could not complete the command.", file=sys.stderr
        )
        return 2


def _show_resilience_json(
    scenario: Scenario,
    engine_version: str,
    run_id: str,
    source: str,
    assertions: tuple[Assertion, ...],
    details: dict[str, object],
) -> None:
    print(
        json.dumps(
            {
                "schema_version": "2",
                "engine_version": engine_version,
                "run_id": run_id,
                "scenario_digest": f"sha256:{scenario.digest}",
                **(
                    {"generation": asdict(scenario.generation)}
                    if scenario.generation is not None
                    else {}
                ),
                **details,
                "cases": [
                    {
                        "case": scenario.name,
                        "operation": asdict(DEPENDENCY_OPERATION),
                        "source": source,
                        "assertions": [asdict(assertion) for assertion in assertions],
                    }
                ],
                "counts": counts(assertions),
            },
            indent=2,
        )
    )


def _show_resilience_check(
    scenario: Scenario,
    trace: ProxyTrace,
    observation: ClientObservation,
    engine_version: str,
    output_format: str,
) -> int:
    assertions = assess_client(scenario, trace, observation)
    if output_format == "json":
        _show_resilience_json(
            scenario,
            engine_version,
            trace.run_id,
            "live_proxy",
            assertions,
            {
                "final_status": observation.status,
                "client_elapsed_ms": observation.elapsed_ms,
                "dependency_attempts": trace.attempts,
                "upstream_requests_sent": trace.upstream_requests_sent,
                "client_observation_digest": trace.observation_digest,
            },
        )
    else:
        print(f"{scenario.name} · proxy run {trace.run_id}")
        for assertion in assertions:
            print(f"  {assertion.verdict} {assertion.rule} · {assertion.reason}")
        print(format_counts(counts(assertions)))
    return resilience_exit_code(assertions)


def _show_resilience(
    scenario: Scenario, client: str, engine_version: str, output_format: str
) -> int:
    run = run_scenario(
        scenario, partial(demo_client, attempts=3 if client == "retry" else 1)
    )
    if output_format == "json":
        _show_resilience_json(
            scenario,
            engine_version,
            str(uuid4()),
            "simulated_http",
            run.assertions,
            {
                "client": client,
                "final_status": run.final_status,
                "simulated_elapsed_ms": run.elapsed_ms,
                "events": [asdict(event) for event in run.events],
            },
        )
    else:
        print(f"{scenario.name} · {client} client · simulated HTTP")
        for event in run.events:
            outcome = (
                f"HTTP {event.outcome}"
                if isinstance(event.outcome, int)
                else event.outcome
            )
            print(
                f"  Attempt {event.attempt} · {outcome} · {event.elapsed_ms} ms simulated"
            )
        for assertion in run.assertions:
            print(f"  {assertion.verdict} {assertion.rule} · {assertion.reason}")
        print(format_counts(counts(run.assertions)))
    return run.exit_code


def _show_check(result: CheckResult, output_format: str) -> int:
    if output_format == "json":
        print(json.dumps(result.to_report(), indent=2))
    else:
        assertion = result.assertion
        field = "" if assertion.field is None else f" at {assertion.field or '(root)'}"
        print(
            f"{assertion.verdict} {_operation_name(result.operation)} · {assertion.rule}{_terminal(field)}"
        )
        print(
            f"{assertion.reason} Observation: imported fixture, HTTP {result.status}."
        )
        for detail in result.assertions:
            if detail is not assertion:
                print(f"  {detail.verdict} {detail.rule} · {detail.reason}")
        print(format_counts(counts(result.assertions)))
    return result.exit_code


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
            profile = (
                f" · {case.auth_profile} expects {case.expected_access}"
                if case.auth_profile
                else ""
            )
            if case.reason is not None:
                print(
                    f"  SKIPPED {case.name} [{case.source}]{profile} · {case.reason.replace('_', ' ')}"
                )
            else:
                print(
                    f"  PLANNED {case.name} [{case.source}]{profile} · "
                    f"{case.request_cost} requests, {len(case.candidate_rules)} candidate assertions"
                )
                for rule in case.candidate_rules:
                    print(f"    {rule.rule} · {rule.condition}")
    print(
        f"{len(plan.operations)} operations · {plan.cases_planned} planned cases · "
        f"{plan.cases_skipped} skipped · {plan.requests_planned}/{plan.request_budget} requests · "
        f"{plan.candidate_assertions} candidate assertions"
    )


def _scan_proxy_reference(
    trace: Path | None, observation: Path | None, *inputs: Path
) -> ProxyReference | None:
    if trace is None and observation is None:
        return None
    if trace is None or observation is None:
        raise InputError(
            "A correlated scan requires both --proxy-trace and --proxy-observation."
        )
    if observation.resolve() in {path.resolve() for path in (trace, *inputs)}:
        raise InputError(
            "The proxy observation output must differ from all scan inputs."
        )
    return ProxyReference.load(trace)


def _show_scan(
    run: ScanRun,
    digest: str,
    engine_version: str,
    output_format: str,
    *,
    proxy: ProxyReference | None = None,
) -> None:
    totals = counts(assertion for case in run.cases for assertion in case.assertions)
    if output_format == "json":
        payload = asdict(run)
        del payload["measurement"]
        if proxy is not None and run.measurement is not None:
            payload["proxy_observation"] = {
                **asdict(proxy),
                "case": run.measurement.case,
                "client_observation_digest": run.measurement.observation.digest,
            }
        print(
            json.dumps(
                {
                    "schema_version": "2",
                    "engine_version": engine_version,
                    "run_id": str(uuid4()),
                    "contract_digest": f"sha256:{digest}",
                    **payload,
                    "counts": totals,
                },
                indent=2,
            )
        )
        return
    for case in run.cases:
        profile = f" [{case.auth_profile}]" if case.auth_profile else ""
        print(
            f"{case.state} {_operation_name(case.operation)} · {case.case}{profile} · {case.reason}"
        )
        for assertion in case.assertions:
            print(f"  {assertion.verdict} {assertion.rule} · {assertion.reason}")
    print(
        f"{run.requests_sent} requests sent · "
        f"{run.requests_attempted}/{run.request_budget} attempts"
    )
    if proxy is not None and run.measurement is not None:
        print(f"Proxy run {proxy.run_id} · {run.measurement.case}")
    print(format_counts(totals))


if __name__ == "__main__":
    raise SystemExit(main())
