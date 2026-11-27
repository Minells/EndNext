import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict

import pytest
from test_check import ENTRY_POINT, EXAMPLES, SENTINEL, run_cli
from test_scan import listener

from endnext.inputs import InputError
from endnext.resilience import Scenario
from endnext.resilience_scenario import materialize_scenario

TEMPLATE = EXAMPLES / "resilience-template.toml"


def test_seeded_cli_replays_byte_identical_scenarios_offline(tmp_path):
    first = run_cli("resilience-generate", TEMPLATE, "--seed", 42, offline=True)
    again = run_cli("resilience-generate", TEMPLATE, "--seed", 42, offline=True)
    assert first.returncode == again.returncode == 0
    assert first.stdout == again.stdout and not first.stderr and not again.stderr
    concrete = tmp_path / "scenario.toml"
    concrete.write_text(first.stdout)
    scenario = Scenario.load(concrete)
    assert [(step.outcome, step.delay_ms) for step in scenario.steps] == [
        (503, 75),
        (429, 60),
        (200, 25),
    ]
    assert scenario.generation is not None
    assert asdict(scenario.generation) == {
        "seed": 42,
        "algorithm": "sha256-v1",
        "template_digest": f"sha256:{hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()}",
    }
    baseline = run_cli("resilience", concrete, "--format", "json", offline=True)
    current = run_cli(
        "resilience", concrete, "--client", "single", "--format", "json", offline=True
    )
    assert (baseline.returncode, current.returncode) == (0, 1)
    saved = []
    for label, captured in (("baseline", baseline), ("current", current)):
        evidence = json.loads(captured.stdout)
        assert evidence["generation"] == asdict(scenario.generation)
        assert evidence["scenario_digest"] == f"sha256:{scenario.digest}"
        path = tmp_path / f"{label}.json"
        path.write_text(captured.stdout)
        saved.append(path)
    diff = run_cli("diff", *saved, "--format", "json", offline=True)
    assert diff.returncode == 1
    assert json.loads(diff.stdout)["counts"]["new_failure"] == 2


def test_different_seed_can_select_other_outcomes(tmp_path):
    scenario_path = tmp_path / "seeded.toml"
    scenario_path.write_text(materialize_scenario(TEMPLATE, 7))
    scenario = Scenario.load(scenario_path)
    assert [step.outcome for step in scenario.steps] == ["disconnect", 503, 200]
    assert scenario.generation is not None and scenario.generation.seed == 7
    passed = run_cli("resilience", scenario_path, "--format", "json", offline=True)
    assert passed.returncode == 0
    assert [event["outcome"] for event in json.loads(passed.stdout)["events"]] == [
        "disconnect",
        503,
        200,
    ]


@pytest.mark.parametrize("seed", [0, 2**63 - 1])
def test_seed_bounds_are_inclusive(tmp_path, seed):
    path = tmp_path / "scenario.toml"
    path.write_text(materialize_scenario(TEMPLATE, seed))
    generation = Scenario.load(path).generation
    assert generation is not None and generation.seed == seed


@pytest.mark.parametrize("seed", [-1, 2**63, True])
def test_out_of_range_seed_is_rejected(seed):
    with pytest.raises(InputError, match="seed"):
        materialize_scenario(TEMPLATE, seed)


def test_a_template_requires_materialization_before_execution():
    result = run_cli("resilience", TEMPLATE, offline=True)
    assert result.returncode == 2 and not result.stdout
    assert "resilience-generate before replaying" in result.stderr


@pytest.mark.parametrize(
    "body",
    [
        "alternatives = []",
        "alternatives = 503",
        "alternatives = [{outcome = 503}]",
        "alternatives = [{outcome = 503},{outcome = 429}]\noutcome = 503",
        'alternatives = [{outcome = 503},{outcome = "unsupported"}]',
        "alternatives = [{outcome = 503},{outcome = 429, delay_ms = 5001}]",
        "alternatives = [" + ",".join("{outcome = 503}" for _ in range(21)) + "]",
    ],
)
def test_all_alternatives_are_validated_before_selection(tmp_path, body):
    header = (
        TEMPLATE.read_text()
        .split("[[steps]]", 1)[0]
        .replace("min_attempts = 3", "min_attempts = 1")
    )
    path = tmp_path / "template.toml"
    path.write_text(header + "[[steps]]\n" + body)
    with pytest.raises(InputError):
        materialize_scenario(path, 42)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("[generation]", "[[generation]]"),
        ('algorithm = "sha256-v1"', 'algorithm = "unknown"'),
        ("seed = 42", "seed = true"),
        ('template_digest = "sha256:', 'template_digest = "'),
        ("seed = 42", "seed = 42\nextra = true"),
    ],
)
def test_generation_metadata_is_validated(tmp_path, before, after):
    path = tmp_path / "generated.toml"
    path.write_text(materialize_scenario(TEMPLATE, 42).replace(before, after))
    with pytest.raises(InputError):
        Scenario.load(path)


def test_a_concrete_scenario_cannot_be_presented_as_its_own_template(tmp_path):
    path = tmp_path / "generated.toml"
    path.write_text(materialize_scenario(TEMPLATE, 42))
    with pytest.raises(InputError, match="cannot contain generation metadata"):
        materialize_scenario(path, 42)


def test_seeded_live_trace_carries_provenance_and_rejects_another_replay(tmp_path):
    template = tmp_path / "forward-template.toml"
    template.write_text(
        TEMPLATE.read_text()
        .replace("outcome = 200", 'outcome = "forward"')
        .replace("budget_ms = 500", "budget_ms = 2000")
    )
    scenario = tmp_path / "live.toml"
    scenario.write_text(materialize_scenario(template, 42))
    output = tmp_path / "live"
    with listener(lambda _: (200, {}, b"healthy")) as (origin, received):
        result = subprocess.run(
            [
                sys.executable,
                str(EXAMPLES / "resilience_live_demo.py"),
                "retry",
                str(output),
                "--scenario",
                str(scenario),
                "--upstream",
                origin,
            ],
            env=os.environ
            | {"PATH": f"{ENTRY_POINT.parent}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
    assert result.returncode == 0, result.stderr
    assert len(received) == 1
    trace, observation = output / "proxy.jsonl", output / "client.json"
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    ready = records[0]
    assert ready["generation"]["seed"] == 42
    assessment = run_cli(
        "resilience-check",
        scenario,
        trace,
        observation,
        "--format",
        "json",
        offline=True,
    )
    assert assessment.returncode == 0 and not assessment.stderr
    evidence = json.loads(assessment.stdout)
    assert evidence["generation"] == ready["generation"]
    assert evidence["scenario_digest"] == ready["scenario_digest"]

    other = tmp_path / "other-seed.toml"
    other.write_text(materialize_scenario(template, 7))
    wrong_scenario = run_cli(
        "resilience-check", other, trace, observation, offline=True
    )
    assert wrong_scenario.returncode == 2 and not wrong_scenario.stdout
    assert "does not match the supplied scenario" in wrong_scenario.stderr
    ready["generation"]["template_digest"] = SENTINEL
    trace.write_text("".join(json.dumps(record) + "\n" for record in records))
    wrong_provenance = run_cli(
        "resilience-check", scenario, trace, observation, offline=True
    )
    assert wrong_provenance.returncode == 2 and not wrong_provenance.stdout
    assert SENTINEL not in wrong_provenance.stderr
