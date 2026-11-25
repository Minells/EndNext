import json

from test_check import CONTRACT, SENTINEL, run_cli, write_json


def test_inspect_text_shows_declared_security_and_request_media_types(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    spec["security"] = [{"token": []}]
    spec["components"]["securitySchemes"] = {
        "token": {"type": "http", "scheme": "bearer"}
    }
    spec["components"]["requestBodies"] = {
        "Payload": {"content": {"application/json": {"schema": {"type": "object"}}}}
    }
    spec["paths"]["/items"] = {
        "post": {
            "security": [],
            "requestBody": {"$ref": "#/components/requestBodies/Payload"},
            "responses": {"204": {"description": "Created"}},
        }
    }
    spec["paths"]["x-inventory-note"] = {}
    result = run_cli(
        "inspect", write_json(tmp_path / "inspect.json", spec), offline=True
    )
    assert result.returncode == 0 and not result.stderr
    assert 'Contract security [{"token": []}]' in result.stdout
    assert "POST /items" in result.stdout
    assert "Request application/json" in result.stdout
    assert "Response 204" in result.stdout
    assert "Operation security []" in result.stdout
    assert "x-inventory-note" not in result.stdout


def test_inspect_reports_when_no_paths_are_declared(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    del spec["paths"]
    result = run_cli(
        "inspect", write_json(tmp_path / "inspect.json", spec), offline=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "No operations declared in paths."


def test_plan_text_shows_available_skipped_and_unconfigured_cases(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    spec["paths"]["/empty"] = {"get": {"responses": {"204": {"description": "Empty"}}}}
    result = run_cli(
        "plan",
        write_json(tmp_path / "openapi.json", spec),
        CONTRACT.parent / "items-plan.toml",
        offline=True,
    )
    assert result.returncode == 0 and not result.stderr
    assert "GET /empty (excluded, anonymous allowed)" in result.stdout
    assert "No cases configured." in result.stdout
    assert "PLANNED imported-item [fixture]" in result.stdout
    assert "PLANNED live-item [http]" in result.stdout
    assert "SKIPPED missing-item [fixture] · fixture missing" in result.stdout
    assert "2 operations · 2 planned cases · 1 skipped · 1/1 requests" in result.stdout


def test_cli_argument_error_does_not_echo_values():
    result = run_cli("check", "--unknown", SENTINEL, offline=True)
    assert result.returncode == 2
    assert not result.stdout
    assert "Invalid arguments" in result.stderr
    assert SENTINEL not in result.stderr


def test_rejects_unknown_schema_dialect_before_resolution(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    spec["jsonSchemaDialect"] = "https://example.invalid/other-dialect"
    result = run_cli(
        "inspect", write_json(tmp_path / "openapi.json", spec), offline=True
    )
    assert result.returncode == 2 and not result.stdout
    assert "unsupported schema dialect" in result.stderr


def test_contract_requires_version(tmp_path):
    spec = json.loads(CONTRACT.read_text())
    del spec["openapi"]
    result = run_cli(
        "inspect", write_json(tmp_path / "openapi.json", spec), offline=True
    )
    assert result.returncode == 2 and not result.stdout
    assert "requires an OpenAPI version" in result.stderr
