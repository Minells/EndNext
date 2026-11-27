import json
import socket
from dataclasses import replace

import httpx
import pytest
from test_check import EXAMPLES, SENTINEL, run_cli, write_json
from test_resilience_proxy import SCENARIO, _finish, _start
from test_scan import listener

from endnext.evidence import VERDICTS
from endnext.inputs import InputError
from endnext.resilience import demo_client
from endnext.resilience_evidence import ClientObservation

CONTRACT = EXAMPLES / "items.openapi.json"
SETTINGS = (EXAMPLES / "resilience-scan.toml").read_text()
RUN_ID = "4a9aa467-bd67-4a9d-8f4b-2eaa983b561b"
SCAN_ID = "bf7d4567-8951-472a-a8fc-b712bdf19f3a"
SCENARIO_DIGEST = "sha256:" + "a" * 64


def _plan(path, origin):
    path.write_text(SETTINGS.replace("http://127.0.0.1:8768", origin))
    return path


def _scan(plan, trace, observation, *, output_format="json", offline=False):
    return run_cli(
        "scan",
        CONTRACT,
        plan,
        "--proxy-trace",
        trace,
        "--proxy-observation",
        observation,
        "--format",
        output_format,
        offline=offline,
    )


def _join(scan_report, assessment, *, output_format="json"):
    return run_cli(
        "correlate", scan_report, assessment, "--format", output_format, offline=True
    )


def test_scan_and_resilience_correlate_a_real_proxy_run(tmp_path):
    reports = {}
    with listener(lambda _: (200, {}, b"demo")) as (upstream, forwarded):
        for label, attempts, status, code in (
            ("baseline", 3, 200, 0),
            ("current", 1, 503, 1),
        ):
            run_dir = tmp_path / label
            run_dir.mkdir()
            observation = run_dir / "client.json"
            trace = run_dir / "proxy.jsonl"
            with _start(upstream, observation=observation, ready_path=trace) as (
                process,
                proxy_url,
            ):

                def api(_, attempts=attempts):
                    with httpx.Client(
                        base_url=proxy_url.removesuffix("/dependency"), trust_env=False
                    ) as dependency:
                        received = demo_client(dependency, attempts=attempts)
                    body = b'{"quantity": 3}' if received == 200 else b""
                    headers = {"Content-Type": "application/json"} if body else {}
                    return received, headers, body

                with listener(api) as (origin, received):
                    scanned = _scan(
                        _plan(run_dir / "scan.toml", origin), trace, observation
                    )
                assert scanned.returncode == code and not scanned.stderr
                assert len(received) == 1
                events = _finish(process, 0)
            with trace.open("a") as log:
                for event in events:
                    log.write(json.dumps(event) + "\n")
            measured = ClientObservation.load(observation)
            assert measured.status == status and measured.elapsed_ms >= 70
            assert measured.digest == events[-1]["client_observation_digest"]
            assessed = run_cli(
                "resilience-check",
                SCENARIO,
                trace,
                observation,
                "--format",
                "json",
                offline=True,
            )
            assert assessed.returncode == code and not assessed.stderr
            reports[label] = (
                write_json(run_dir / "scan.json", json.loads(scanned.stdout)),
                write_json(run_dir / "resilience.json", json.loads(assessed.stdout)),
            )
    assert len(forwarded) == 1
    baseline, current = reports["baseline"], reports["current"]
    joined = _join(*current)
    assert joined.returncode == 0 and not joined.stderr
    output = json.loads(joined.stdout)
    assert output["case"] == "item-detail"
    assert output["scan_state"] == "FAIL"
    assert output["operation"]["path_template"] == "/items/{id}"
    assert {finding["rule"] for finding in output["findings"]} == {
        "response.status.documented",
        "response.status.expected",
    }
    assert {
        entry["rule"]: entry["verdict"] for entry in output["resilience_assertions"]
    } == {
        "resilience.final_status": "FAIL",
        "resilience.attempts": "FAIL",
        "resilience.recovery_time": "SKIPPED",
    }
    previous = _join(*baseline, output_format="text")
    assert (
        previous.returncode == 0
        and "0 scan findings in this proxy run" in previous.stdout
    )
    assert "PASS resilience.recovery_time" in previous.stdout
    shown = _join(*current, output_format="text")
    assert shown.returncode == 0 and "FAIL response.status.expected" in shown.stdout
    diff = run_cli("diff", baseline[0], current[0], "--format", "json", offline=True)
    assert diff.returncode == 1
    failures = {
        change["current"]["fingerprint"]
        for change in json.loads(diff.stdout)["changes"]
        if change["kind"] == "new_failure"
    }
    assert failures == {finding["fingerprint"] for finding in output["findings"]}
    mismatched = _join(baseline[0], current[1])
    assert mismatched.returncode == 2 and not mismatched.stdout
    assert "same proxy run and client measurement" in mismatched.stderr


def _ready(path):
    path.write_text(
        json.dumps(
            {
                "event": "ready",
                "trace_version": "1",
                "run_id": RUN_ID,
                "scenario_digest": SCENARIO_DIGEST,
            }
        )
        + "\n"
    )
    return path


def test_bad_correlation_flags_prevent_http_dispatch(tmp_path):
    plan = _plan(tmp_path / "scan.toml", "http://127.0.0.1:8768")
    trace = _ready(tmp_path / "proxy.jsonl")
    for flags in (
        ("--proxy-trace", trace),
        ("--proxy-observation", tmp_path / "client.json"),
    ):
        result = run_cli("scan", CONTRACT, plan, *flags, offline=True)
        assert result.returncode == 2 and "requires both" in result.stderr
    alias = _scan(plan, trace, trace, offline=True)
    assert alias.returncode == 2 and "must differ" in alias.stderr


@pytest.mark.parametrize("budget", [0, 2])
def test_requires_exactly_one_planned_http_case(tmp_path, budget):
    plan = _plan(tmp_path / "scan.toml", "http://127.0.0.1:8768")
    second = SETTINGS.split("[[cases]]", 1)[1].replace("item-detail", "second-item")
    plan.write_text(
        plan.read_text().replace("request_budget = 1", f"request_budget = {budget}")
        + "\n[[cases]]"
        + second
    )
    result = _scan(
        plan, _ready(tmp_path / "proxy.jsonl"), tmp_path / "observed.json", offline=True
    )
    assert (
        result.returncode == 2 and "exactly one eligible live request" in result.stderr
    )


def test_unsent_request_produces_no_proxy_measurement(tmp_path):
    with socket.socket() as unlistened:
        unlistened.bind(("127.0.0.1", 0))
        plan = _plan(
            tmp_path / "scan.toml", f"http://127.0.0.1:{unlistened.getsockname()[1]}"
        )
        observation = tmp_path / "observed.json"
        result = _scan(plan, _ready(tmp_path / "proxy.jsonl"), observation)
    assert result.returncode == 2 and not observation.exists()
    report = json.loads(result.stdout)
    assert report["requests_sent"] == 0 and "proxy_observation" not in report


def test_text_scan_saves_the_observation_and_shows_the_proxy_run(tmp_path):
    with listener(
        lambda _: (200, {"Content-Type": "application/json"}, b'{"quantity": 1}')
    ) as (origin, _):
        plan = _plan(tmp_path / "scan.toml", origin)
        observation = tmp_path / "observed.json"
        result = _scan(
            plan, _ready(tmp_path / "proxy.jsonl"), observation, output_format="text"
        )
    assert result.returncode == 0 and f"Proxy run {RUN_ID}" in result.stdout
    assert ClientObservation.load(observation).run_id == RUN_ID


def _document(run_id, source, *, verdict):
    return {
        "schema_version": "2",
        "run_id": run_id,
        "cases": [
            {
                "case": "item-detail",
                "source": source,
                "operation": {"method": "GET", "path_template": "/items/{id}"},
                "assertions": [
                    {
                        "rule": "response.status.expected",
                        "verdict": verdict,
                        "expectation": "Expected status",
                        "reason": SENTINEL,
                    }
                ],
            }
        ],
        "counts": dict.fromkeys(VERDICTS, 0) | {verdict: 1},
    }


def _documents():
    observed = ClientObservation(RUN_ID, 503, 150)
    scan = _document(SCAN_ID, "live_http", verdict="FAIL") | {
        "requests_attempted": 1,
        "requests_sent": 1,
        "proxy_observation": {
            "run_id": RUN_ID,
            "case": "item-detail",
            "scenario_digest": SCENARIO_DIGEST,
            "client_observation_digest": observed.digest,
        },
    }
    resilience = _document(RUN_ID, "live_proxy", verdict="FAIL") | {
        "scenario_digest": SCENARIO_DIGEST,
        "final_status": observed.status,
        "client_elapsed_ms": observed.elapsed_ms,
        "dependency_attempts": 1,
        "client_observation_digest": observed.digest,
    }
    return scan, resilience


@pytest.mark.parametrize(
    "kind",
    [
        "legacy",
        "case",
        "digest",
        "elapsed",
        "error",
        "simulated",
        "invalid_id",
        "no_status",
        "no_attempts",
    ],
)
def test_rejects_unbound_or_unusable_evidence(tmp_path, kind):
    scan, resilience = _documents()
    if kind == "legacy":
        scan.pop("proxy_observation")
    elif kind == "case":
        scan["proxy_observation"]["case"] = SENTINEL
    elif kind == "digest":
        resilience["scenario_digest"] = "sha256:" + "b" * 64
    elif kind == "elapsed":
        resilience["client_elapsed_ms"] = 151
    elif kind == "error":
        resilience = resilience | _document(RUN_ID, "live_proxy", verdict="ERROR")
    elif kind == "simulated":
        resilience["cases"][0]["source"] = "simulated_http"
    elif kind == "no_status":
        resilience.pop("final_status")
    elif kind == "no_attempts":
        resilience["dependency_attempts"] = 0
    else:
        scan["proxy_observation"]["run_id"] = SENTINEL
    result = _join(
        write_json(tmp_path / "scan.json", scan),
        write_json(tmp_path / "resilience.json", resilience),
    )
    assert result.returncode == 2 and not result.stdout
    assert "ERROR [input]" in result.stderr and SENTINEL not in result.stderr


def test_observation_output_replaces_atomically_and_reports_io_errors(tmp_path):
    observation = ClientObservation(RUN_ID, 503, 150)
    output = tmp_path / "observation.json"
    observation.save(output)
    latest = replace(observation, elapsed_ms=200)
    latest.save(output)
    assert ClientObservation.load(output) == latest
    with pytest.raises(InputError, match="could not be written"):
        latest.save(tmp_path / "missing" / "observation.json")
