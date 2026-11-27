import json
from dataclasses import replace

import httpx
import pytest
from test_check import EXAMPLES, SENTINEL, run_cli

from endnext.inputs import InputError
from endnext.resilience import Scenario, run_scenario

DEMO = EXAMPLES / "resilience-retry.toml"
SETTINGS = DEMO.read_text(encoding="utf-8")


def _run(path=DEMO, *, client="retry", output_format="json"):
    return run_cli(
        "resilience", path, "--client", client, "--format", output_format, offline=True
    )


def test_demo_replays_offline_and_compares_both_clients(tmp_path):
    passed = _run()
    failed = _run(client="single")
    assert (passed.returncode, failed.returncode) == (0, 1)
    assert not passed.stderr and not failed.stderr
    healthy = json.loads(passed.stdout)
    repeat = json.loads(_run().stdout)
    assert healthy.pop("run_id") != repeat.pop("run_id")
    assert healthy == repeat
    assert healthy["scenario_digest"].startswith("sha256:")
    assert healthy["final_status"] == 200
    assert healthy["simulated_elapsed_ms"] == 200
    assert healthy["events"] == [
        {"attempt": 1, "outcome": 503, "elapsed_ms": 75},
        {"attempt": 2, "outcome": 429, "elapsed_ms": 175},
        {"attempt": 3, "outcome": 200, "elapsed_ms": 200},
    ]
    broken = json.loads(failed.stdout)
    assert broken["scenario_digest"] == healthy["scenario_digest"]
    assert broken["final_status"] == 503
    assert broken["events"] == healthy["events"][:1]
    assert broken["counts"]["FAIL"] == 2 and broken["counts"]["SKIPPED"] == 1
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(passed.stdout)
    current.write_text(failed.stdout)

    report = run_cli("report", current, "--format", "markdown", offline=True)
    assert report.returncode == 0
    assert r"simulated\_http | FAIL | resilience.final\_status" in report.stdout
    delta = run_cli("diff", baseline, current, "--format", "json", offline=True)
    assert delta.returncode == 1
    changes = json.loads(delta.stdout)
    assert changes["counts"]["new_failure"] == 2
    assert changes["counts"]["unverified"] == 1
    assert changes["compared_checks"] == 2


def test_transport_faults_contribute_to_simulated_latency(tmp_path):
    scenario = tmp_path / "faults.toml"
    scenario.write_text(
        SETTINGS.replace("outcome = 503", 'outcome = "timeout"').replace(
            "outcome = 429", 'outcome = "disconnect"'
        )
    )
    retry = _run(scenario, output_format="text")
    assert retry.returncode == 0 and not retry.stderr
    assert "Attempt 1 · timeout · 75 ms simulated" in retry.stdout
    assert "Attempt 2 · disconnect · 175 ms simulated" in retry.stdout
    assert "Attempt 3 · HTTP 200 · 200 ms simulated" in retry.stdout
    single = _run(scenario, client="single")
    assert single.returncode == 1
    assert json.loads(single.stdout)["final_status"] is None


def test_success_can_exceed_the_retry_limit_and_recovery_budget(tmp_path):
    scenario = tmp_path / "over-budget.toml"
    scenario.write_text(
        SETTINGS.replace("min_attempts = 3", "min_attempts = 1")
        .replace("max_attempts = 3", "max_attempts = 2")
        .replace("recovery_budget_ms = 250", "recovery_budget_ms = 199")
    )
    result = _run(scenario)
    assert result.returncode == 1
    (case,) = json.loads(result.stdout)["cases"]
    assert {entry["rule"]: entry["verdict"] for entry in case["assertions"]} == {
        "resilience.final_status": "PASS",
        "resilience.attempts": "FAIL",
        "resilience.recovery_time": "FAIL",
    }


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('name = "dependency-retry"', 'name = "invalid name"'),
        ('name = "dependency-retry"', 'origin = "http://example.invalid"'),
        ("expected_status = 200", "expected_status = 503"),
        ("min_attempts = 3", "min_attempts = 0"),
        ("max_attempts = 3", "max_attempts = 2"),
        ("min_attempts = 3", "min_attempts = 4"),
        ("max_attempts = 3", "max_attempts = 21"),
        ("recovery_budget_ms = 250", "recovery_budget_ms = true"),
        ("[[steps]]", "[[stepz]]"),
        ("delay_ms = 75", "unknown = 75"),
        ("outcome = 503", "outcome = 100"),
        ("outcome = 429", 'outcome = "hang"'),
        ("delay_ms = 75", "delay_ms = 5001"),
    ],
)
def test_rejects_invalid_scenario_fields(tmp_path, before, after):
    scenario = tmp_path / "invalid.toml"
    scenario.write_text(SETTINGS.replace(before, after))
    with pytest.raises(InputError):
        Scenario.load(scenario)


@pytest.mark.parametrize("num_steps", [2, 13, 21])
def test_bounds_scenario_length_and_total_latency(tmp_path, num_steps):
    scenario = tmp_path / "bounds.toml"
    header = SETTINGS.split("[[steps]]", 1)[0]
    scenario.write_text(
        header + "\n[[steps]]\noutcome = 200\ndelay_ms = 5000\n" * num_steps
    )
    with pytest.raises(InputError):
        Scenario.load(scenario)


def test_malformed_toml_does_not_echo_input(tmp_path):
    scenario = tmp_path / "malformed.toml"
    scenario.write_text(f"{SENTINEL} = [")
    result = _run(scenario)
    assert result.returncode == 2 and not result.stdout
    assert "ERROR [input]" in result.stderr and SENTINEL not in result.stderr


def test_custom_client_without_attempts_is_inconclusive():
    run = run_scenario(Scenario.load(DEMO), lambda session: 200)
    assert run.exit_code == 2 and run.final_status is None
    assert not run.events
    assert run.assertions[0].verdict == "INCONCLUSIVE"


def test_client_exception_is_sanitized():
    def client(session):
        session.get("/dependency")
        raise RuntimeError(SENTINEL)

    run = run_scenario(Scenario.load(DEMO), client)
    assert run.exit_code == 2 and run.assertions[0].verdict == "ERROR"
    assert len(run.events) == 1
    assert SENTINEL not in repr(run)


def test_client_cannot_consume_steps_for_an_off_scenario_operation():
    def client(session):
        with pytest.raises(httpx.TransportError):
            session.get(f"http://example.invalid/{SENTINEL}")
        with pytest.raises(httpx.TransportError):
            session.get("/dependency")
        return 200

    run = run_scenario(Scenario.load(DEMO), client)
    assert run.exit_code == 2 and not run.events
    assert "outside the simulation" in run.assertions[0].reason
    assert SENTINEL not in repr(run)


def test_exhausted_scenario_is_an_execution_error(tmp_path):
    path = tmp_path / "exhausted.toml"
    header = SETTINGS.split("[[steps]]", 1)[0].replace(
        "min_attempts = 3", "min_attempts = 1"
    )
    path.write_text(header + "\n[[steps]]\noutcome = 503\n")
    result = _run(path)
    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["counts"]["ERROR"] == 1
    assert data["events"] == [{"attempt": 1, "outcome": 503, "elapsed_ms": 0}]


def test_unhandled_simulated_timeout_is_an_observed_failure():
    scenario = Scenario.load(DEMO)
    fault = replace(scenario.steps[0], outcome="timeout")
    scenario = replace(scenario, steps=(fault, *scenario.steps[1:]))
    run = run_scenario(scenario, lambda session: session.get("/dependency").status_code)
    assert run.exit_code == 1 and run.final_status is None
    assert run.events[0].outcome == "timeout"


def test_invalid_return_value_does_not_become_evidence():
    def client(session):
        session.get("/dependency")
        return SENTINEL

    run = run_scenario(Scenario.load(DEMO), client)
    assert run.exit_code == 2 and run.final_status is None
    assert SENTINEL not in repr(run)
