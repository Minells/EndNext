import json
import os
import subprocess
import sys
import sysconfig
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from endnext.cli import main
from endnext.contract import Contract
from endnext.inputs import MAX_BYTES

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CONTRACT = EXAMPLES / "items.openapi.json"
PASS = EXAMPLES / "item-pass.json"
FAIL = EXAMPLES / "item-fail.json"
SENTINEL = json.loads(FAIL.read_text())["body"]["quantity"]
ENTRY_POINT = Path(sysconfig.get_path("scripts")) / (
    "endnext.exe" if os.name == "nt" else "endnext"
)

# Run the installed CLI with network blocked before it imports EndNext. Record
# attempts even if a dependency catches the exception raised by the blocker.
OFFLINE_DRIVER = """
import os
import sys
import urllib.request
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

attempts = []
foreign = sys.argv[1]
package = find_spec("openapi_spec_validator")
metaschema_uri = (
    Path(package.origin).parent / "resources" / "schemas" / "v3.1" / "schema.json"
).as_uri()
original_open = urllib.request.OpenerDirector.open

def guarded(event, args):
    if event.startswith("socket.") or (
        event == "open" and isinstance(args[0], (str, bytes))
        and os.path.basename(os.fsdecode(args[0])) == foreign
    ):
        attempts.append(event)
        raise OSError("test I/O blocked")
def block_url(opener, request, *args, **kwargs):
    if getattr(request, "full_url", request) == metaschema_uri:
        return original_open(opener, request, *args, **kwargs)
    attempts.append("url")
    raise OSError("test I/O blocked")
sys.addaudithook(guarded)
with patch.object(urllib.request.OpenerDirector, "open", block_url):
    try:
        from endnext.cli import main
        raise SystemExit(main(sys.argv[2:]))
    finally:
        if attempts:
            raise AssertionError(f"External I/O attempted: {attempts}")
"""


def cli(contract=CONTRACT, fixture=PASS, *, report_format="json", offline=False):
    return run_cli(
        "check", contract, fixture, "--format", report_format, offline=offline
    )


def run_cli(*arguments, offline=False):
    args = [str(ENTRY_POINT), *map(str, arguments)]
    assert ENTRY_POINT.is_file(), "Install EndNext before running its CLI tests."
    if offline:
        args = [
            sys.executable,
            "-c",
            OFFLINE_DRIVER,
            "prohibited-reference.json",
            *args[1:],
        ]
    return subprocess.run(args, text=True, capture_output=True, timeout=15, check=False)


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def assert_no_sentinel(stdout, stderr):
    assert SENTINEL not in stdout
    assert SENTINEL not in stderr


def test_demo_only_changes_quantity_type():
    good = json.loads(PASS.read_text())
    bad = json.loads(FAIL.read_text())
    assert type(good["body"]["quantity"]) is int
    assert type(bad["body"]["quantity"]) is str
    good["body"]["quantity"] = bad["body"]["quantity"]
    assert good == bad


@pytest.mark.parametrize(
    ("fixture", "verdict", "exit_code"), [(PASS, "PASS", 0), (FAIL, "FAIL", 1)]
)
def test_installed_cli_resolves_internal_schema_offline(fixture, verdict, exit_code):
    completed = cli(fixture=fixture, offline=True)
    assert completed.returncode == exit_code, completed.stderr
    assert not completed.stderr
    assert_no_sentinel(completed.stdout, completed.stderr)
    result = json.loads(completed.stdout)
    assert result["verdict"] == verdict
    assert result["rule"] == "response.body.schema"
    assert result["operation"] == {
        "method": "GET",
        "path_template": "/items/{id}",
        "operation_id": "getItem",
    }
    assert result["source"] == {"kind": "imported_fixture", "fixture": fixture.name}
    assert result["observation"] == {"status": 200, "media_type": "application/json"}
    assert result["schema_version"] and result["engine_version"] and result["run_id"]
    assert result["contract_digest"].startswith("sha256:")
    if fixture == FAIL:
        assert result["field"] == "/quantity"
        assert result["expectation"] == "integer"
        assert result["observed_type"] == "string"
        assert "string" in result["reason"] and "integer" in result["reason"]


def test_terminal_failure_is_readable():
    completed = cli(fixture=FAIL, report_format="text")
    assert completed.returncode == 1
    assert "FAIL GET /items/{id} (getItem)" in completed.stdout
    assert "response.body.schema at /quantity" in completed.stdout
    assert "imported fixture" in completed.stdout
    assert not completed.stderr
    assert_no_sentinel(completed.stdout, completed.stderr)


@pytest.mark.parametrize(
    ("keyword", "location"),
    [
        ("$ref", "https://example.invalid/prohibited-reference.json#/Item"),
        ("$ref", "relative"),
        ("$ref", "file"),
        ("$dynamicRef", "https://example.invalid/prohibited-reference.json#item"),
    ],
)
def test_external_reference_rejected_before_io(tmp_path, keyword, location):
    external = write_json(tmp_path / "prohibited-reference.json", {"type": "object"})
    spec = json.loads(CONTRACT.read_text())
    schema = spec["paths"]["/items/{id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]
    reference = {"relative": "./" + external.name, "file": external.as_uri()}.get(
        location, location
    )
    schema["schema"] = {keyword: reference}
    completed = cli(write_json(tmp_path / "contract.json", spec), FAIL, offline=True)
    assert completed.returncode == 2, completed.stderr
    assert "ERROR [input] External references" in completed.stderr
    assert not completed.stdout
    assert_no_sentinel(completed.stdout, completed.stderr)


def test_schema_id_cannot_rebase_a_fragment(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    spec["components"]["schemas"]["Item"]["$id"] = "https://example.invalid/remote.json"
    completed = cli(write_json(tmp_path / "contract.json", spec), offline=True)
    assert completed.returncode == 2
    assert "Schema $id is unsupported" in completed.stderr


def test_invalid_internal_reference_is_input_error(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    del spec["components"]["schemas"]["Item"]
    completed = cli(write_json(tmp_path / "contract.json", spec), FAIL, offline=True)
    assert completed.returncode == 2
    assert "ERROR [input]" in completed.stderr
    assert_no_sentinel(completed.stdout, completed.stderr)


def test_fixture_chooses_the_named_operation_not_a_matching_concrete_path(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    other = deepcopy(spec["paths"]["/items/{id}"])
    other["get"]["operationId"] = "getSample"
    del other["get"]["parameters"]
    other["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "properties": {"quantity": {"type": "string"}},
    }
    spec["paths"]["/items/sample"] = other
    completed = cli(write_json(tmp_path / "contract.json", spec), FAIL, offline=True)
    assert completed.returncode == 1, completed.stderr
    assert json.loads(completed.stdout)["operation"]["operation_id"] == "getItem"


@pytest.mark.parametrize(
    "problem", ["unreadable", "malformed", "too-large", "unknown-operation"]
)
def test_fixture_input_errors_do_not_disclose_raw_data(tmp_path, problem):
    path = tmp_path / "fixture.json"
    if problem == "malformed":
        path.write_text('{"raw":"' + SENTINEL)
    if problem == "too-large":
        path.write_bytes(b" " * (MAX_BYTES + 1))
    if problem == "unknown-operation":
        fixture = json.loads(FAIL.read_text())
        fixture.update(path_template="/unknown", path_parameters={})
        write_json(path, fixture)
    completed = cli(fixture=path)
    assert completed.returncode == 2
    assert "ERROR [input]" in completed.stderr
    assert not completed.stdout
    assert_no_sentinel(completed.stdout, completed.stderr)


def test_execution_error_does_not_disclose_exception(monkeypatch, capsys):
    def explode(*args):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(Contract, "check", explode)
    assert main(["check", str(CONTRACT), str(FAIL), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert "ERROR [execution]" in captured.err
    assert not captured.out
    assert_no_sentinel(captured.out, captured.err)


@pytest.mark.parametrize("fixture", [PASS, FAIL])
def test_json_and_yaml_produce_equivalent_results(tmp_path, fixture):
    data = json.loads(CONTRACT.read_text())
    contract = tmp_path / "contract.yml"
    contract.write_text(yaml.safe_dump(data), encoding="utf-8")
    from_json = cli(fixture=fixture, offline=True)
    from_yaml = cli(contract, fixture, offline=True)
    assert not from_json.stderr and not from_yaml.stderr
    assert from_yaml.returncode == from_json.returncode
    keys = ("verdict", "rule", "expectation", "field", "operation", "observation")
    json_report, yaml_report = map(json.loads, (from_json.stdout, from_yaml.stdout))
    assert {key: json_report[key] for key in keys} == {
        key: yaml_report[key] for key in keys
    }


def test_inspect_and_check_operation_without_id(tmp_path):
    contract = tmp_path / "weather.yaml"
    contract.write_text(
        """
openapi: 3.1.0
info:
  title: Weather observations
  version: '1'
servers:
  - url: https://example.invalid/v1
security:
  - accessKey: []
paths:
  /forecast:
    $ref: '#/components/pathItems/Forecast'
components:
  securitySchemes:
    accessKey:
      type: apiKey
      in: header
      name: X-Weather-Key
  pathItems:
    Forecast:
      get:
        security: []
        responses:
          '200':
            description: Forecast available
            content:
              application/json:
                schema:
                  type: object
                  required: [on, celsius]
                  properties:
                    on: {type: string}
                    celsius: {type: number}
""".lstrip(),
        encoding="utf-8",
    )
    result = run_cli("inspect", contract, "--format", "json", offline=True)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "declared_security": [{"accessKey": []}],
        "operations": [
            {
                "operation": {
                    "method": "GET",
                    "path_template": "/forecast",
                    "operation_id": None,
                },
                "declared_security": [],
                "request_media_types": [],
                "response_media_types": {"200": ["application/json"]},
            }
        ],
    }
    fixture = write_json(
        tmp_path / "own-response.json",
        {
            "case": "sample-forecast",
            "method": "GET",
            "path_template": "/forecast",
            "status": 200,
            "media_type": "application/json",
            "body": {"on": "2026-11-25", "celsius": 12.5},
        },
    )
    checked = cli(contract, fixture, offline=True)
    assert checked.returncode == 0, checked.stderr
    assert not checked.stderr
    operation = json.loads(checked.stdout)["operation"]
    assert operation["operation_id"] is None
    assert operation["path_template"] == "/forecast"


@pytest.mark.parametrize(
    ("filename", "content", "diagnostic"),
    [
        ("malformed.json", "{", "invalid UTF-8 JSON"),
        ("old.json", '{"openapi":"3.0.3"}', "Only OpenAPI 3.1"),
        ("close.yaml", "openapi: [", "invalid UTF-8 YAML"),
        (
            "bad.yaml",
            "field: !!python/object/apply:builtins.str [secret]",
            "unsupported YAML tag",
        ),
        ("duplicate.yaml", "openapi: 3.1.0\nopenapi: 3.1.1", "duplicate YAML key"),
        ("cyclic.yml", "value: &cycle [*cycle]", "YAML aliases are unsupported"),
        ("large.yaml", " " * (MAX_BYTES + 1), "1 MiB input limit"),
    ],
)
def test_contract_input_error_is_reported_by_inspect(
    tmp_path, filename, content, diagnostic
):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    result = run_cli("inspect", path, offline=True)
    assert result.returncode == 2
    assert "ERROR [input]" in result.stderr and diagnostic in result.stderr
    assert not result.stdout


def test_arbitrary_response_property_is_redacted(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    spec["components"]["schemas"]["Item"] = {
        "additionalProperties": {"type": "integer"}
    }
    contract = write_json(tmp_path / "contract.json", spec)
    fixture = json.loads(FAIL.read_text())
    fixture["body"] = {SENTINEL: SENTINEL}
    response = cli(contract, write_json(tmp_path / "own-response.json", fixture))
    assert response.returncode == 1
    assert json.loads(response.stdout)["field"] == "/[redacted]"
    assert_no_sentinel(response.stdout, response.stderr)


@pytest.mark.parametrize(
    ("schema", "verdict", "code"), [(True, "PASS", 0), (False, "FAIL", 1)]
)
def test_boolean_root_schema_through_internal_reference(
    tmp_path, schema, verdict, code
):
    spec = json.loads(CONTRACT.read_text())
    spec["components"]["schemas"]["Item"] = schema
    response = cli(write_json(tmp_path / "contract.json", spec), FAIL, offline=True)
    assert response.returncode == code, response.stderr
    assert not response.stderr
    assert json.loads(response.stdout)["verdict"] == verdict
    assert_no_sentinel(response.stdout, response.stderr)


def test_json_body_uses_the_most_specific_media_range(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    response = spec["paths"]["/items/{id}"]["get"]["responses"]["200"]
    response["content"] = {
        "*/*": {"schema": False},
        "application/*": response["content"]["application/json"],
    }
    completed = cli(write_json(tmp_path / "contract.json", spec), offline=True)
    assert completed.returncode == 0, completed.stderr
    assert not completed.stderr
    assert json.loads(completed.stdout)["verdict"] == "PASS"


def test_response_without_schema_cannot_pass(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    response = spec["paths"]["/items/{id}"]["get"]["responses"]["200"]
    response["content"]["application/json"] = {}
    completed = cli(write_json(tmp_path / "contract.json", spec), FAIL, offline=True)
    assert completed.returncode == 2
    assert "ERROR [input]" in completed.stderr
    assert "response has no body schema" in completed.stderr
    assert_no_sentinel(completed.stdout, completed.stderr)


def test_plan_is_deterministic_and_offline_with_security_and_budget(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    spec["security"] = [{"apiKey": []}]
    spec["components"]["securitySchemes"] = {
        "apiKey": {"type": "apiKey", "in": "header", "name": "X-Key"}
    }
    item_path = spec["paths"]["/items/{id}"]
    item_path["get"]["security"] = [{"apiKey": []}, {}]
    for path in ("/protected", "/no-case", "/excluded"):
        operation = {"responses": item_path["get"]["responses"]}
        if path == "/no-case":
            operation["security"] = []
        spec["paths"][path] = {"get": operation}
    contract = write_json(tmp_path / "contract.json", spec)
    config = tmp_path / "suite.toml"
    config.write_text(
        f"""
select = ["GET /protected", "GET /items/{{id}}", "GET /no-case"]
request_budget = 1

[[cases]]
name = "z-over-budget"
operation = "GET /items/{{id}}"
source = "http"
expected_status = 200
path_parameters = {{ id = "sample" }}

[[cases]]
name = "b-imported"
operation = "GET /items/{{id}}"
source = "fixture"
fixture = {json.dumps(str(FAIL))}

[[cases]]
name = "a-projected"
operation = "GET /items/{{id}}"
source = "http"
expected_status = 200
path_parameters = {{ id = "sample" }}

[[cases]]
name = "c-missing"
operation = "GET /items/{{id}}"
source = "fixture"
fixture = "absent.json"

[[cases]]
name = "d-no-parameters"
operation = "GET /items/{{id}}"
source = "http"
expected_status = 200

[[cases]]
name = "protected"
operation = "GET /protected"
source = "http"
expected_status = 200

[[cases]]
name = "excluded"
operation = "GET /excluded"
source = "http"
expected_status = 200
""",
        encoding="utf-8",
    )
    first = run_cli("plan", contract, config, "--format", "json", offline=True)
    second = run_cli("plan", contract, config, "--format", "json", offline=True)
    assert first.returncode == second.returncode == 0
    assert not first.stderr and not second.stderr
    assert first.stdout == second.stdout
    assert_no_sentinel(first.stdout, first.stderr)
    result = json.loads(first.stdout)
    operations = {
        entry["operation"]["path_template"]: entry for entry in result["operations"]
    }
    assert list(operations) == ["/excluded", "/items/{id}", "/no-case", "/protected"]
    assert operations["/no-case"]["cases"] == []
    assert operations["/no-case"]["allows_anonymous"] is True
    assert operations["/no-case"]["security_source"] == "operation"
    assert not operations["/protected"]["allows_anonymous"]
    assert operations["/protected"]["security_source"] == "document"
    assert operations["/protected"]["cases"][0]["reason"] == "authentication_required"
    assert operations["/excluded"]["cases"][0]["reason"] == "operation_not_selected"
    item = operations["/items/{id}"]
    assert item["effective_security"] == [{"apiKey": []}, {}]
    assert item["allows_anonymous"]
    assert [case["reason"] for case in item["cases"]] == [
        None,
        None,
        "fixture_missing",
        "path_parameters_missing_or_invalid",
        "request_budget_exhausted",
    ]
    assert [case["request_cost"] for case in item["cases"]] == [1, 0, 0, 0, 0]
    assert [len(case["candidate_rules"]) for case in item["cases"]] == [4, 3, 0, 0, 0]
    assert result["requests_planned"] == result["request_budget"] == 1
    assert result["candidate_assertions"] == 7
    assert result["cases_planned"] == 2
    assert result["cases_skipped"] == 5


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ('select = ["GET /unknown"]', "selected operation is absent"),
        ("select = []\nrequest_budget = -1", "request budget"),
        (
            'select = []\n[[cases]]\nname = "case"\noperation = "GET /items/{id}"\nsource = "fixture"\nfixture = "https://example.invalid/a.json"',
            "local filesystem",
        ),
    ],
)
def test_plan_rejects_invalid_input(tmp_path, config, message):
    path = tmp_path / "config.toml"
    path.write_text(config, encoding="utf-8")
    result = run_cli("plan", CONTRACT, path, offline=True)
    assert result.returncode == 2, result.stderr
    assert "ERROR [input]" in result.stderr and message in result.stderr
    assert not result.stdout
