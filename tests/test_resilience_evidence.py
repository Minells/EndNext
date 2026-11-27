import json
import os
import subprocess
import sys
from dataclasses import replace

import httpx
import pytest
from test_check import ENTRY_POINT, EXAMPLES, SENTINEL, run_cli, write_json
from test_resilience_proxy import SCENARIO, _finish, _start
from test_scan import listener

from endnext.inputs import InputError
from endnext.resilience import Scenario
from endnext.resilience_evidence import ClientObservation, ProxyTrace, assess_client

RUN_ID = "5b921718-ce50-4489-a5df-fd738d3f7ee4"
OTHER_ID = "15fec129-24d1-4883-b430-a18d96de247d"


def _records(status=200, elapsed_ms=200):
    scenario = Scenario.load(SCENARIO, allow_forward=True)
    return [
        {
            "event": "ready",
            "run_id": RUN_ID,
            "trace_version": "1",
            "case": scenario.name,
            "scenario_digest": f"sha256:{scenario.digest}",
            "origin": SENTINEL,
        },
        {
            "event": "step",
            "run_id": RUN_ID,
            "attempt": 1,
            "outcome": 503,
            "status": 503,
            "elapsed_ms": 75,
            "problem": None,
        },
        {
            "event": "step",
            "run_id": RUN_ID,
            "attempt": 2,
            "outcome": 429,
            "status": 429,
            "elapsed_ms": 175,
            "problem": None,
        },
        {
            "event": "step",
            "run_id": RUN_ID,
            "attempt": 3,
            "outcome": "forward",
            "status": 200,
            "elapsed_ms": 200,
            "problem": None,
        },
        {
            "event": "finished",
            "run_id": RUN_ID,
            "reason": "complete",
            "attempts": 3,
            "upstream_requests_sent": 1,
            "client_observation_digest": ClientObservation(
                RUN_ID, status, elapsed_ms
            ).digest,
        },
    ]


def _trace(directory, records):
    path = directory / "proxy.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def _check(trace, observation, output="json"):
    return run_cli(
        "resilience-check",
        SCENARIO,
        trace,
        observation,
        "--format",
        output,
        offline=True,
    )


def test_live_demo_produces_comparable_reports(tmp_path):
    reports = {}
    with listener(lambda _: (200, {"Content-Type": "text/plain"}, b"demo")) as (
        origin,
        received,
    ):
        for client, exit_code, attempts, status, reason in (
            ("retry", 0, 3, 200, "complete"),
            ("single", 1, 1, 503, "client_finished"),
        ):
            directory = tmp_path / client
            capture = subprocess.run(
                [
                    sys.executable,
                    str(EXAMPLES / "resilience_live_demo.py"),
                    client,
                    str(directory),
                    "--upstream",
                    origin,
                ],
                env=os.environ
                | {"PATH": f"{ENTRY_POINT.parent}{os.pathsep}{os.environ['PATH']}"},
                text=True,
                capture_output=True,
                timeout=40,
                check=False,
            )
            assert capture.returncode == 0, capture.stderr
            trace, observed = directory / "proxy.jsonl", directory / "client.json"
            records = [json.loads(line) for line in trace.read_text().splitlines()]
            assert records[-1]["reason"] == reason
            assert (
                records[-1]["client_observation_digest"]
                == ClientObservation.load(observed).digest
            )
            assert {record["run_id"] for record in records} == {
                json.loads(observed.read_text())["run_id"]
            }
            checked = _check(trace, observed)
            assert checked.returncode == exit_code and not checked.stderr
            result = json.loads(checked.stdout)
            assert result["run_id"] == records[0]["run_id"]
            assert (
                result["dependency_attempts"] == attempts
                and result["final_status"] == status
            )
            assert result["client_elapsed_ms"] > 0
            assert result["cases"][0]["source"] == "live_proxy"
            reports[client] = write_json(directory / "evidence.json", result)
    assert len(received) == 1
    delta = run_cli(
        "diff", reports["retry"], reports["single"], "--format", "json", offline=True
    )
    assert delta.returncode == 1
    assert json.loads(delta.stdout)["counts"]["new_failure"] == 2
    rendered = run_cli("report", reports["single"], offline=True)
    assert (
        rendered.returncode == 0
        and "live_proxy · FAIL · resilience.final_status" in rendered.stdout
    )


def test_recovery_budget_uses_client_measurement_and_ignores_unused_fields(tmp_path):
    trace = _trace(tmp_path, _records(elapsed_ms=2001))
    observation = write_json(
        tmp_path / "client.json", {"run_id": RUN_ID, "status": 200, "elapsed_ms": 2001}
    )
    result = _check(trace, observation)
    assert result.returncode == 1 and not result.stderr
    assert SENTINEL not in result.stdout
    data = json.loads(result.stdout)
    assert data["counts"]["PASS"] == 2 and data["counts"]["FAIL"] == 1
    text = _check(trace, observation, "text")
    assert text.returncode == 1
    assert (
        "FAIL resilience.recovery_time · Observed recovery after 2001 ms of client elapsed time."
        in text.stdout
    )
    assert SENTINEL not in text.stdout


@pytest.mark.parametrize(("status", "code"), [(200, 0), (None, 1)])
def test_client_outcome_is_separate_from_proxy_status(tmp_path, status, code):
    trace = _trace(tmp_path, _records(status, 2000))
    observation = write_json(
        tmp_path / "client.json",
        {"run_id": RUN_ID, "status": status, "elapsed_ms": 2000},
    )
    result = _check(trace, observation)
    assert result.returncode == code
    assert json.loads(result.stdout)["final_status"] == status


@pytest.mark.parametrize(
    "state",
    [
        "truncated",
        "interrupted",
        "no_attempts",
        "deadline",
        "failed",
        "invalid_observation",
        "unconfirmed",
        "exhausted",
    ],
)
def test_unfinished_or_failed_run_does_not_produce_client_verdicts(tmp_path, state):
    records = _records()
    if state != "no_attempts":
        records[-1]["client_observation_digest"] = None
    if state == "truncated":
        records.pop()
    elif state in {"interrupted", "deadline"}:
        reason = "interrupted" if state == "interrupted" else "run_deadline_exhausted"
        records = [
            *records[:2],
            records[-1]
            | {"reason": reason, "attempts": 2, "upstream_requests_sent": 0},
        ]
    elif state == "no_attempts":
        records = [
            records[0],
            records[-1]
            | {"reason": "client_finished", "attempts": 0, "upstream_requests_sent": 0},
        ]
    elif state == "failed":
        records[3].update(status=502, problem=SENTINEL)
        records[-1]["reason"] = SENTINEL
    elif state == "invalid_observation":
        records = [
            records[0],
            records[-1]
            | {
                "reason": "client_observation_invalid",
                "attempts": 0,
                "upstream_requests_sent": 0,
            },
        ]
    elif state == "exhausted":
        records[-1].update(attempts=4, reason="scenario_exhausted")
    trace = _trace(tmp_path, records)
    observation = write_json(
        tmp_path / "client.json", {"run_id": RUN_ID, "status": 200, "elapsed_ms": 200}
    )
    result = _check(trace, observation)
    assert result.returncode == 2 and not result.stderr
    assert SENTINEL not in result.stdout
    data = json.loads(result.stdout)
    (assertion,) = data["cases"][0]["assertions"]
    assert assertion["rule"] == "resilience.execution"
    assert assertion["verdict"] == (
        "ERROR"
        if state in {"failed", "invalid_observation", "exhausted"}
        else "INCONCLUSIVE"
    )


def test_proxy_rejects_unusable_observation_while_waiting(tmp_path):
    observation = tmp_path / "client.json"
    with _start("http://127.0.0.1:8767", observation=observation) as (process, _):
        pending = observation.with_suffix(".tmp")
        pending.write_text(SENTINEL)
        pending.replace(observation)
        (finished,) = _finish(process, 2)
    assert finished["reason"] == "client_observation_invalid"


def test_proxy_observes_overrun_before_client_completion(tmp_path):
    observation = tmp_path / "client.json"
    with listener(lambda _: (200, {}, b"demo")) as (origin, received):
        with (
            _start(origin, observation=observation) as (process, url),
            httpx.Client(trust_env=False) as client,
        ):
            assert [client.get(url).status_code for _ in range(4)] == [
                503,
                429,
                200,
                502,
            ]
            *steps, finished = _finish(process, 2)
    assert len(steps) == 3 and len(received) == 1
    assert finished["attempts"] == 4 and finished["reason"] == "scenario_exhausted"
    assert finished["client_observation_digest"] is None


@pytest.mark.parametrize(
    ("line", "field", "value"),
    [
        (0, "event", "step"),
        (0, "trace_version", None),
        (0, "run_id", "invalid"),
        (0, "case", "different"),
        (0, "scenario_digest", "sha256:bad"),
        (1, "run_id", OTHER_ID),
        (1, "event", "finished"),
        (1, "attempt", True),
        (1, "attempt", 2),
        (1, "outcome", 503.0),
        (1, "outcome", 500),
        (1, "status", None),
        (1, "status", False),
        (2, "elapsed_ms", 74),
        (2, "elapsed_ms", -1),
        (2, "problem", []),
        (2, "problem", "early error"),
        (3, "status", None),
        (4, "attempts", 1),
        (4, "attempts", 4),
        (4, "reason", ""),
        (4, "client_observation_digest", "bad-digest"),
        (4, "upstream_requests_sent", 2),
        (4, "upstream_requests_sent", 0),
    ],
)
def test_trace_rejects_mismatched_or_inconsistent_metadata(
    tmp_path, line, field, value
):
    records = _records()
    records[line][field] = value
    with pytest.raises(InputError):
        ProxyTrace.load(
            _trace(tmp_path, records), Scenario.load(SCENARIO, allow_forward=True)
        )


@pytest.mark.parametrize(
    "records", [[], _records() + [_records()[-1]], _records()[:-1] + [_records()[3]]]
)
def test_trace_rejects_empty_or_excess_records(tmp_path, records):
    with pytest.raises(InputError):
        ProxyTrace.load(
            _trace(tmp_path, records), Scenario.load(SCENARIO, allow_forward=True)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", SENTINEL),
        ("status", "503"),
        ("status", 100),
        ("elapsed_ms", False),
        ("elapsed_ms", -1),
        ("extra", SENTINEL),
    ],
)
def test_observation_validation_is_sanitized(tmp_path, field, value):
    path = write_json(
        tmp_path / "client.json",
        {"run_id": RUN_ID, "status": 200, "elapsed_ms": 200} | {field: value},
    )
    with pytest.raises(InputError) as error:
        ClientObservation.load(path)
    assert SENTINEL not in str(error.value)


def test_client_observation_from_another_run_is_rejected(tmp_path):
    trace = _trace(tmp_path, _records())
    bad = write_json(
        tmp_path / "client.json", {"run_id": OTHER_ID, "status": 200, "elapsed_ms": 220}
    )
    result = _check(trace, bad)
    assert result.returncode == 2 and not result.stdout
    assert "different proxy run" in result.stderr and OTHER_ID not in result.stderr


def test_changed_measurement_with_matching_run_id_is_rejected(tmp_path):
    trace = _trace(tmp_path, _records())
    changed = write_json(
        tmp_path / "client.json", {"run_id": RUN_ID, "status": 503, "elapsed_ms": 200}
    )
    result = _check(trace, changed)
    assert result.returncode == 2 and not result.stdout
    assert "does not match the observation recorded by the proxy" in result.stderr


def test_scripted_transport_fault_can_be_assessed_without_http_status(tmp_path):
    scenario = Scenario.load(SCENARIO, allow_forward=True)
    steps = (replace(scenario.steps[0], outcome="disconnect"), *scenario.steps[1:])
    scenario = replace(scenario, steps=steps)
    records = _records(None, 210)
    records[1].update(outcome="disconnect", status=None)
    records[3].update(outcome="forward", status=200)
    trace = ProxyTrace.load(_trace(tmp_path, records), scenario)
    assertions = assess_client(scenario, trace, ClientObservation(RUN_ID, None, 210))
    assert [assertion.verdict for assertion in assertions] == [
        "FAIL",
        "PASS",
        "SKIPPED",
    ]
