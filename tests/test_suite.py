import json

from test_check import CONTRACT, PASS, run_cli, write_json

from endnext.cli import main
from endnext.contract import Contract


def _case(name, fixture=None, expected_status=None):
    source = "http" if fixture is None else "fixture"
    text = f"""
[[cases]]
name = "{name}"
operation = "GET /items/{{id}}"
source = "{source}"
"""
    if fixture is not None:
        text += f'fixture = "{fixture}"\n'
    if expected_status is not None:
        text += f"expected_status = {expected_status}\n"
    return text


def _config(tmp_path, *cases, selected='["GET /items/{id}"]'):
    plan = tmp_path / "suite.toml"
    plan.write_text(f"select = {selected}\n" + "".join(cases))
    return plan


def _suite(plan, *, output_format="json"):
    return run_cli("suite", CONTRACT, plan, "--format", output_format, offline=True)


def _by_case(result):
    assert not result.stderr
    return {case["case"]: case for case in json.loads(result.stdout)["cases"]}


def test_suite_to_report_and_diff_with_missing_fixture_and_failure(tmp_path):
    data = json.loads(PASS.read_text())
    first = write_json(tmp_path / "first.json", data)
    second = write_json(tmp_path / "second.json", data)
    plan = _config(tmp_path, _case("first", first.name), _case("second", second.name))
    baseline = tmp_path / "baseline.json"
    result = _suite(plan)
    assert result.returncode == 0
    assert set(_by_case(result)) == {"first", "second"}
    baseline.write_text(result.stdout)

    data["body"]["quantity"] = "bad"
    write_json(first, data)
    regression = _suite(plan)
    assert regression.returncode == 1
    for missing, result in [(False, regression), (True, None)]:
        if missing:
            second.unlink()
            result = _suite(plan)
        assert result.returncode == 1
        current = tmp_path / "current.json"
        current.write_text(result.stdout)
        rendered = run_cli("report", current, "--format", "markdown", offline=True)
        assert rendered.returncode == 0 and "/quantity" in rendered.stdout
        diff = run_cli("diff", baseline, current, "--format", "json", offline=True)
        assert diff.returncode == 1
        comparison = json.loads(diff.stdout)
        assert comparison["blocking_regressions"]
        assert comparison["incomplete_verification"] == missing
        changes = comparison["changes"]
        (failure,) = [change for change in changes if change["kind"] == "new_failure"]
        assert failure["check"]["case"] == "first"
        assert failure["current"]["field"] == "/quantity"
        assert any(change["kind"] == "unverified" for change in changes) == missing
        if missing:
            assert "Set fixture to a readable local JSON file." in rendered.stdout


def test_suite_plan_expectations_and_independent_cases(tmp_path):
    fixture = json.loads(PASS.read_text())
    write_json(tmp_path / "pass.json", fixture)
    write_json(tmp_path / "expected.json", fixture | {"expected_status": 200})
    (tmp_path / "invalid.json").write_text("invalid JSON")
    changed = fixture | {"method": "HEAD"}
    write_json(tmp_path / "different.json", changed)
    plan = _config(
        tmp_path,
        _case("from-plan", "pass.json", 200),
        _case("from-fixture", "expected.json"),
        _case("conflict", "expected.json", 404),
        _case("http-only", expected_status=200),
        _case("invalid", "invalid.json"),
        _case("different-operation", "different.json"),
    )
    result = _suite(plan)
    assert result.returncode == 2
    records = _by_case(result)
    for checked in ("from-fixture", "from-plan"):
        assert records[checked]["assertions"][-1]["rule"] == "response.status.expected"
        assert records[checked]["assertions"][-1]["verdict"] == "PASS"
    (conflict,) = records["conflict"]["assertions"]
    assert conflict["field"] == "expected_status"
    assert conflict["verdict"] == "INCONCLUSIVE"
    assert records["http-only"]["assertions"][0]["verdict"] == "SKIPPED"
    assert records["http-only"]["source"]["kind"] == "live_http"
    assert "fixture_invalid" in records["invalid"]["assertions"][0]["reason"]
    assert (
        "fixture_operation_mismatch"
        in records["different-operation"]["assertions"][0]["reason"]
    )
    text = _suite(plan, output_format="text")
    assert text.returncode == 2 and "expected_status" in text.stdout


def test_suite_loads_contract_once_and_reads_each_fixture_once(tmp_path, monkeypatch):
    from endnext.inputs import Fixture

    fixture = write_json(tmp_path / "pass.json", json.loads(PASS.read_text()))
    plan = _config(
        tmp_path, _case("first", fixture.name), _case("second", fixture.name)
    )
    contract_reads = []
    fixture_reads = []
    for cls, reads in ((Contract, contract_reads), (Fixture, fixture_reads)):

        def loader(load, seen):
            def tracked(path):
                seen.append(path)
                return load(path)

            return tracked

        monkeypatch.setattr(cls, "load", loader(cls.load, reads))
    assert main(["suite", str(CONTRACT), str(plan), "--format", "json"]) == 0
    assert contract_reads == [CONTRACT]
    assert fixture_reads == [fixture]


def test_suite_empty_and_intentionally_skipped_cases(tmp_path):
    plan = _config(tmp_path)
    result = _suite(plan)
    assert result.returncode == 2 and _by_case(result) == {}
    (operation,) = json.loads(result.stdout)["coverage"]["operations"]
    assert (
        operation["selected"] and operation["incomplete"] and not operation["executed"]
    )
    plan = _config(
        tmp_path, _case("live"), _case("unselected", "missing.json"), selected="[]"
    )
    result = _suite(plan)
    assert result.returncode == 2
    assert all(
        record["assertions"][0]["verdict"] == "SKIPPED"
        for record in _by_case(result).values()
    )


def test_suite_retains_bound_and_preserves_response_without_schema(
    tmp_path, monkeypatch, capsys
):
    document = json.loads(CONTRACT.read_text())
    content = document["paths"]["/items/{id}"]["get"]["responses"]["200"]["content"]
    del content["application/json"]["schema"]
    contract = write_json(tmp_path / "schema.json", document)
    write_json(tmp_path / "pass.json", json.loads(PASS.read_text()))
    plan = _config(tmp_path, _case("first", "pass.json"))
    result = run_cli("suite", contract, plan, "--format", "json", offline=True)
    assert result.returncode == 2
    assert _by_case(result)["first"]["assertions"][0]["rule"] == "fixture.verify"
    monkeypatch.setattr("endnext.suite.MAX_BYTES", 3)
    assert main(["suite", str(CONTRACT), str(plan), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "Split the plan" in captured.err


def test_suite_coverage_distinguishes_contract_selection_execution_and_incomplete(
    tmp_path,
):
    document = json.loads(CONTRACT.read_text())
    for name in ("/no-case", "/unselected"):
        document["paths"][name] = {"get": {"responses": {"200": {"description": "OK"}}}}
    contract = write_json(tmp_path / "contract.json", document)
    write_json(tmp_path / "pass.json", json.loads(PASS.read_text()))
    plan = _config(
        tmp_path,
        _case("first", "pass.json"),
        _case("offline-skipped"),
        selected='["GET /items/{id}", "GET /no-case"]',
    )
    result = run_cli("suite", contract, plan, "--format", "json", offline=True)
    assert result.returncode == 0
    by_path = {
        item["operation"]["path_template"]: (
            item["selected"],
            item["executed"],
            item["incomplete"],
        )
        for item in json.loads(result.stdout)["coverage"]["operations"]
    }
    assert by_path == {
        "/items/{id}": (True, True, True),
        "/no-case": (True, False, True),
        "/unselected": (False, False, False),
    }
    report = tmp_path / "report.json"
    report.write_text(result.stdout, encoding="utf-8")
    summary = tmp_path / "summary.md"
    diff = run_cli(
        "diff",
        report,
        report,
        "--format",
        "json",
        "--summary-file",
        summary,
        offline=True,
    )
    assert diff.returncode == 2
    result_json = json.loads(diff.stdout)
    assert (
        result_json["incomplete_verification"]
        and not result_json["blocking_regressions"]
    )
    assert result_json["counts"]["unverified"] == 0
    text = summary.read_text(encoding="utf-8")
    assert "3 contract operations" in text and "2 selected" in text
    assert "1/2 selected operations with executed checks" in text
    assert "2/2 selected operations with incomplete verification" in text
    assert "/no\\-case" in text
