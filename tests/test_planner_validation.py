import json

import pytest
from test_check import CONTRACT, PASS, SENTINEL, write_json

from endnext.contract import Contract
from endnext.inputs import InputError
from endnext.planner import PlanConfig, build_plan

OPERATION = "GET /items/{id}"
HTTP_CASE = f'name = "case"\noperation = "{OPERATION}"\nsource = "http"\n'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("select = [", "invalid UTF-8 TOML"),
        ("select = 1", "requires a select array"),
        (
            f'select = ["{OPERATION}", "get /items/{{id}}"]',
            "selects the same operation",
        ),
        ('select = ["GET items/{id}"]', "exact path template"),
        ("select = []\ncases = {}", "array of tables"),
        ("select = []\ncases = [1]", "Each plan case must be a table"),
        (
            "select = []\n[[cases]]\n" + HTTP_CASE + "\n[[cases]]\n" + HTTP_CASE,
            "case name more than once",
        ),
        ("select = []\n[[cases]]\n", "requires a valid name"),
        (
            'select = []\n[[cases]]\nname = "test"\nsource = "manual"',
            "source must be fixture or http",
        ),
        (
            "select = []\n[[cases]]\n" + HTTP_CASE + "expected_status = true",
            "expected_status must be an integer",
        ),
        (
            "select = []\n[[cases]]\n" + HTTP_CASE + 'fixture = "local.json"',
            "Only fixture cases",
        ),
        (
            "select = []\n[[cases]]\n" + HTTP_CASE + "query_parameters = []",
            "parameters must be a table",
        ),
        (
            "select = []\n[[cases]]\n" + HTTP_CASE + "query_parameters = { n = true }",
            "parameters must be strings or finite numbers",
        ),
        (
            "select = []\n[[cases]]\n" + HTTP_CASE + "query_parameters = { n = inf }",
            "parameters must be strings or finite numbers",
        ),
        (
            "select = []\n[[cases]]\n"
            + HTTP_CASE.replace('"http"', '"fixture"')
            + 'path_parameters = { id = "demo" }',
            "Imported parameters belong in the fixture",
        ),
    ],
)
def test_plan_rejects_ambiguous_configuration(tmp_path, text, message):
    config = tmp_path / "plan.toml"
    config.write_text(text)
    with pytest.raises(InputError, match=message):
        PlanConfig.load(config)


def test_plan_distinguishes_unusable_cases_without_spending_budget(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text(SENTINEL)
    wrong = json.loads(PASS.read_text())
    wrong["method"] = "POST"
    write_json(tmp_path / "wrong.json", wrong)
    config = tmp_path / "plan.toml"
    config.write_text(f"""
select = ["{OPERATION}"]
request_budget = 1

[[cases]]
name = "first"
operation = "{OPERATION}"
source = "fixture"
fixture = "invalid.json"

[[cases]]
name = "second"
operation = "{OPERATION}"
source = "fixture"
fixture = "wrong.json"

[[cases]]
name = "third"
operation = "{OPERATION}"
source = "http"
expected_status = 404
path_parameters = {{ id = "sample" }}
""")
    result = build_plan(Contract.load(CONTRACT).inventory(), PlanConfig.load(config))
    assert result.requests_planned == result.cases_planned == 0
    assert result.cases_skipped == 3
    assert [case.reason for case in result.operations[0].cases] == [
        "fixture_invalid",
        "fixture_operation_mismatch",
        "http_requires_positive_get",
    ]


def test_plan_rejects_case_for_unknown_operation(tmp_path):
    config = tmp_path / "plan.toml"
    config.write_text(
        "select = []\n[[cases]]\n" + HTTP_CASE.replace(OPERATION, "GET /missing")
    )
    with pytest.raises(InputError, match="case operation is absent"):
        build_plan(Contract.load(CONTRACT).inventory(), PlanConfig.load(config))
