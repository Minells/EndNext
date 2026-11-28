import json

import pytest
from test_check import CONTRACT, PASS, SENTINEL, cli, run_cli, write_json

from endnext.contract import Contract
from endnext.inputs import Fixture, InputError
from endnext.reporting import SavedReport
from endnext.rules import verify


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


@pytest.mark.parametrize(
    ("status", "overrides", "expected"),
    [
        (
            200,
            {"media_type": "text/plain"},
            {
                "response.media_type.documented": "FAIL",
                "response.body.schema": "SKIPPED",
            },
        ),
        (
            200,
            {"body_state": "absent", "media_type": None},
            {
                "response.media_type.documented": "SKIPPED",
                "response.body.schema": "FAIL",
            },
        ),
        (
            200,
            {"body_state": "truncated", "media_type": None},
            {
                "response.media_type.documented": "INCONCLUSIVE",
                "response.body.schema": "INCONCLUSIVE",
            },
        ),
        (204, {}, {"response.body.schema": "FAIL"}),
        (
            204,
            {"body_state": "truncated", "media_type": None},
            {"response.body.schema": "INCONCLUSIVE"},
        ),
        (
            403,
            {"identity": "anonymous", "expected_access": False},
            {"security.anonymous_access": "PASS"},
        ),
        (
            500,
            {"identity": "anonymous", "expected_access": True},
            {"security.anonymous_access": "INCONCLUSIVE"},
        ),
        (
            200,
            {
                "sensitive": True,
                "expected_cache_directive": "no-store",
                "headers": {"cache-control": 'private="a,b", No-Store'},
            },
            {"security.cache_directive": "PASS"},
        ),
    ],
)
def test_rule_verdicts_remain_independent_when_observations_change(
    tmp_path, status, overrides, expected
):
    spec = json.loads(CONTRACT.read_text())
    responses = spec["paths"]["/items/{id}"]["get"]["responses"]
    responses.update(
        {
            "204": {"description": "No Content"},
            "403": responses["200"],
            "500": responses["200"],
        }
    )
    contract = Contract.load(write_json(tmp_path / "contract.json", spec))
    fixture = Fixture.load(
        _fixture(tmp_path, status=status, expected_status=status, **overrides)
    )
    _, assertions = verify(contract, fixture, imported=True)
    verdicts = {assertion.rule: assertion.verdict for assertion in assertions}
    assert (
        verdicts["response.status.documented"]
        == verdicts["response.status.expected"]
        == "PASS"
    )
    assert {rule: verdicts[rule] for rule in expected} == expected


@pytest.mark.parametrize("missing_schema", [True, False])
def test_live_observation_keeps_status_when_schema_cannot_run(tmp_path, missing_schema):
    spec = json.loads(CONTRACT.read_text())
    content = spec["paths"]["/items/{id}"]["get"]["responses"]["200"]["content"]
    if missing_schema:
        content["application/json"] = {}
        media_type = "application/json"
    else:
        content["text/plain"] = content.pop("application/json")
        media_type = "text/plain"
    observation = Fixture.load(
        _fixture(tmp_path, media_type=media_type, expected_status=200)
    )
    _, assertions = verify(
        Contract.load(write_json(tmp_path / "contract.json", spec)),
        observation,
        imported=False,
    )
    assert {assertion.rule: assertion.verdict for assertion in assertions} == {
        "response.status.documented": "PASS",
        "response.status.expected": "PASS",
        "response.media_type.documented": "PASS",
        "response.body.schema": "INCONCLUSIVE",
    }


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"schema_version": "0"}, "Unsupported EndNext report schema version"),
        ({"cases": {}}, "invalid cases"),
        ({"cases": [None]}, "invalid record"),
        ({"requests_sent": True}, "invalid request count"),
        ({"operation": {"method": "GET"}}, "invalid text metadata"),
        ({"assertions": [{"verdict": "UNKNOWN"}]}, "unknown verdict"),
        ({"assertions": None}, "invalid assertions"),
        ({"counts": {"ERROR": 0}}, "counts do not match"),
        (
            {
                "counts": {
                    "ERROR": 0,
                    "INCONCLUSIVE": 0,
                    "FAIL": 0,
                    "WARN": 0,
                    "PASS": True,
                    "SKIPPED": 0,
                }
            },
            "counts do not match",
        ),
    ],
)
def test_saved_report_rejects_invalid_metadata(tmp_path, updates, message):
    record = {
        "schema_version": "2",
        "case": "sample",
        "source": "live_http",
        "operation": {"method": "GET", "path_template": "/"},
        "assertions": [
            {"rule": "status", "verdict": "PASS", "reason": "OK", "expectation": "200"}
        ],
        "counts": {
            "ERROR": 0,
            "INCONCLUSIVE": 0,
            "FAIL": 0,
            "WARN": 0,
            "PASS": 1,
            "SKIPPED": 0,
        },
    }
    record.update(updates)
    path = write_json(tmp_path / "report.json", record)
    with pytest.raises(InputError, match=message):
        SavedReport.load(path)


def test_replay_v1_skipped_case_without_assertions(tmp_path):
    path = write_json(
        tmp_path / "old.json",
        {
            "schema_version": "1",
            "requests_sent": 0,
            "cases": [
                {
                    "case": "old",
                    "operation": {"method": "GET", "path_template": "/"},
                    "source": "live_http",
                    "state": "SKIPPED",
                    "reason": "not selected",
                }
            ],
        },
    )
    result = run_cli("report", path, offline=True)
    assert result.returncode == 0 and not result.stderr
    assert "SKIPPED 1 · 0 requests sent" in result.stdout
    assert "live_http · SKIPPED · case.outcome ·  · not selected" in result.stdout


def test_authenticated_fixture_explicit_denial_has_verdict(tmp_path):
    fixture = _fixture(tmp_path, identity="authenticated", expected_access=False)
    result = cli(fixture=fixture)
    assert result.returncode == 1
    assertions = json.loads(result.stdout)["assertions"]
    assert any(
        assertion["rule"] == "security.authenticated_access"
        and assertion["verdict"] == "FAIL"
        for assertion in assertions
    )


def test_cache_directive_requires_sensitive_fixture(tmp_path):
    fixture = _fixture(tmp_path, expected_cache_directive="no-store")
    result = cli(fixture=fixture)
    assert result.returncode == 2
    assert "expected_cache_directive" in result.stderr
    assert "sensitive" in result.stderr


@pytest.mark.parametrize(
    ("method", "status"),
    [("HEAD", 200), ("GET", 204), ("GET", 205), ("GET", 304)],
)
@pytest.mark.parametrize(
    ("body_state", "verdict", "exit_code"),
    [(None, "SKIPPED", 0), ("complete", "FAIL", 1), ("truncated", "INCONCLUSIVE", 2)],
)
def test_bodyless_response_semantics(
    tmp_path, method, status, body_state, verdict, exit_code
):
    spec = json.loads(CONTRACT.read_text())
    item = spec["paths"]["/items/{id}"]
    if method == "HEAD":
        item["head"] = item.pop("get")
    responses = item[method.lower()]["responses"]
    if status != 200:
        responses[str(status)] = {"description": "No content"}
    data = json.loads(PASS.read_text())
    data.update(method=method, status=status)
    if body_state != "complete":
        data.pop("body")
    if body_state is not None:
        data["body_state"] = body_state
    fixture = write_json(tmp_path / "fixture.json", data)
    result = cli(write_json(tmp_path / "openapi.json", spec), fixture)
    assert result.returncode == exit_code, result.stderr
    assertions = {
        assertion["rule"]: assertion["verdict"]
        for assertion in json.loads(result.stdout)["assertions"]
    }
    assert assertions["response.body.schema"] == verdict
    assert assertions["response.media_type.documented"] == "SKIPPED"


@pytest.mark.parametrize(
    ("media_type", "status", "response_key", "content_key"),
    [
        ("application/problem+json", 400, "400", "application/problem+json"),
        ("application/vnd.items+json", 200, "2XX", "application/*"),
        ("text/example+json", 400, "default", "*/*"),
    ],
)
def test_structured_json_schema_selection(
    tmp_path, media_type, status, response_key, content_key
):
    from endnext.api_v1 import FixtureChecker

    spec = json.loads(CONTRACT.read_text())
    responses = spec["paths"]["/items/{id}"]["get"]["responses"]
    response = responses.pop("200")
    media = response["content"].pop("application/json")
    response["content"][content_key] = media
    if content_key == media_type:
        response["content"]["application/*"] = {"schema": {"type": "object"}}
    responses[response_key] = response
    contract = write_json(tmp_path / "openapi.json", spec)
    fixture = _fixture(
        tmp_path, media_type=media_type, status=status, body={"quantity": "bad"}
    )
    result = cli(contract, fixture, offline=True)
    assert result.returncode == 1, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["rule"] == "response.body.schema"
    assert evidence["field"] == "/quantity"
    check = FixtureChecker(contract).check(fixture)
    assert check.assertion.field == "/quantity"
    assert check.exit_code == 1


@pytest.mark.parametrize(("status", "verdict"), [(403, "PASS"), (401, "INCONCLUSIVE")])
def test_imported_authenticated_denial_distinguishes_unauthenticated(
    tmp_path, status, verdict
):
    spec = json.loads(CONTRACT.read_text())
    responses = spec["paths"]["/items/{id}"]["get"]["responses"]
    responses[str(status)] = responses["200"]
    contract = write_json(tmp_path / "openapi.json", spec)
    fixture = _fixture(
        tmp_path, status=status, identity="authenticated", expected_access=False
    )
    result = cli(contract, fixture)
    assert result.returncode == (0 if verdict == "PASS" else 2), result.stderr
    assertions = json.loads(result.stdout)["assertions"]
    assert any(
        assertion["rule"] == "security.authenticated_access"
        and assertion["verdict"] == verdict
        for assertion in assertions
    )
