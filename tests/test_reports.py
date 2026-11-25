import json

import pytest
from test_check import CONTRACT, PASS, SENTINEL, cli, run_cli, write_json


def _fixture(tmp_path, **overrides):
    data = json.loads(PASS.read_text())
    data.update(overrides)
    if data.get("body_state") in {"absent", "truncated", "invalid"}:
        data.pop("body")
    return write_json(tmp_path / "recorded.json", data)


def test_documented_500_can_match_schema_and_fail_owner_expectation(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    responses = spec["paths"]["/items/{id}"]["get"]["responses"]
    responses["500"] = responses["200"]
    contract = write_json(tmp_path / "openapi.json", spec)
    observation = _fixture(tmp_path, status=500, expected_status=200)
    result = cli(contract, observation, offline=True)
    assert result.returncode == 1
    data = json.loads(result.stdout)
    verdicts = {item["rule"]: item["verdict"] for item in data["assertions"]}
    assert verdicts == {
        "response.status.documented": "PASS",
        "response.media_type.documented": "PASS",
        "response.body.schema": "PASS",
        "response.status.expected": "FAIL",
    }
    assert data["counts"]["PASS"] == 3 and data["counts"]["FAIL"] == 1
    path = tmp_path / "evidence.json"
    path.write_text(result.stdout)
    direct = cli(contract, observation, report_format="text", offline=True)
    for fmt in ("text", "markdown"):
        rendered = run_cli("report", path, "--format", fmt, offline=True)
        assert rendered.returncode == 0, rendered.stderr
        assert "response.status.expected" in rendered.stdout
        assert "imported" in rendered.stdout
        assert rendered.stdout.splitlines()[0] == direct.stdout.splitlines()[-1]


@pytest.mark.parametrize("status,match", [(201, "2XX"), (400, "default")])
def test_check_response_range_and_default(tmp_path, status, match):
    spec = json.loads(CONTRACT.read_text())
    responses = spec["paths"]["/items/{id}"]["get"]["responses"]
    responses[match] = responses.pop("200")
    case = _fixture(tmp_path, status=status, expected_status=status)
    result = cli(write_json(tmp_path / "openapi.json", spec), case, offline=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["counts"]["PASS"] == 4


def test_bodyless_204_passes_status_without_claiming_schema_pass(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    responses = spec["paths"]["/items/{id}"]["get"]["responses"]
    responses["204"] = {"description": "No Content"}
    case = _fixture(tmp_path, status=204, body_state="absent", media_type=None)
    result = cli(write_json(tmp_path / "openapi.json", spec), case, offline=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["counts"]["PASS"] == 1 and data["counts"]["SKIPPED"] == 3


@pytest.mark.parametrize("body_state", ["truncated", "invalid"])
def test_partial_body_preserves_status_verdict(tmp_path, body_state):
    case = _fixture(tmp_path, body_state=body_state, expected_status=200)
    result = cli(fixture=case, offline=True)
    assert result.returncode == 2 and not result.stderr
    data = json.loads(result.stdout)
    assert data["counts"]["INCONCLUSIVE"] == 1 and data["counts"]["PASS"] == 3


def test_imported_access_and_cache_findings_hide_values(tmp_path):
    case = _fixture(
        tmp_path,
        identity="anonymous",
        expected_access=False,
        sensitive=True,
        expected_cache_directive="no-store",
        headers={"Cache-Control": f'private="{SENTINEL}"', "Set-Cookie": SENTINEL},
        query_parameters={"sensitive": SENTINEL},
        body={"quantity": 1, "unreported": SENTINEL},
    )
    result = cli(fixture=case, offline=True)
    assert result.returncode == 1, result.stderr
    assert SENTINEL not in result.stdout + result.stderr
    data = json.loads(result.stdout)
    verdicts = {item["rule"]: item["verdict"] for item in data["assertions"]}
    assert verdicts["security.anonymous_access"] == "FAIL"
    assert verdicts["security.cache_directive"] == "WARN"
    assert verdicts["response.status.expected"] == "SKIPPED"


def test_replay_v1_report_escapes_markdown_and_preserves_provenance(tmp_path):
    original = {
        "schema_version": "1",
        "case": "old-case",
        "operation": {
            "method": "GET",
            "path_template": "/items",
            "operation_id": "legacy|id",
        },
        "source": {"kind": "imported_fixture", "fixture": "response.json"},
        "rule": "response.body.schema",
        "verdict": "FAIL",
        "field": "/quantity",
        "expectation": "integer",
        "reason": "Bad | row\n<script>alert(1)</script> **bold**",
    }
    path = write_json(tmp_path / "legacy.json", original)
    rendered = run_cli("report", path, "--format", "markdown", offline=True)
    assert rendered.returncode == 0, rendered.stderr
    assert "FAIL 1" in rendered.stdout
    assert "imported\\_fixture" in rendered.stdout
    assert "legacy\\|id" in rendered.stdout and "<script>" not in rendered.stdout
    assert "Bad \\| row" in rendered.stdout and "&lt;script&gt;" in rendered.stdout
    assert "\\*\\*bold\\*\\*" in rendered.stdout
