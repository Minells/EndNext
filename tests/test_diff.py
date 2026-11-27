import json

import pytest
from test_check import FAIL, PASS, SENTINEL, cli, run_cli, write_json


def _record(
    name, verdict, *, field=None, source="imported_fixture", operation_id="getItem"
):
    return {
        "case": name,
        "operation": {
            "method": "GET",
            "path_template": "/items/{id}",
            "operation_id": operation_id,
        },
        "source": {"kind": source, "fixture": f"{operation_id}.json"},
        "assertion": {
            "rule": "response.body.schema",
            "verdict": verdict,
            "field": field,
            "expectation": "A schema",
            "reason": SENTINEL,
        },
    }


def _saved_reports(tmp_path, baseline, current):
    return (
        write_json(
            tmp_path / "baseline.json", {"schema_version": "1", "cases": baseline}
        ),
        write_json(
            tmp_path / "current.json", {"schema_version": "1", "cases": current}
        ),
    )


def _diff(baseline, current, *, output_format="json"):
    return run_cli("diff", baseline, current, "--format", output_format, offline=True)


def test_diff_installed_cli_compares_actual_check_reports_offline(tmp_path):
    good = cli(fixture=PASS, offline=True)
    bad = cli(fixture=FAIL, offline=True)
    assert (good.returncode, bad.returncode) == (0, 1)
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "current.json"
    baseline.write_text(good.stdout)
    current.write_text(bad.stdout)

    result = _diff(baseline, current)
    assert result.returncode == 1 and not result.stderr
    assert SENTINEL not in result.stdout
    data = json.loads(result.stdout)
    assert data["exit_code"] == 1 and data["compared_checks"] == 3
    assert data["counts"]["new_failure"] == 1
    (change,) = data["changes"]
    assert change["check"]["path_template"] == "/items/{id}"
    assert change["check"]["rule"] == "response.body.schema"
    assert change["current"]["field"] == "/quantity"
    assert change["current"]["fingerprint"].startswith("sha256:")

    text_report = _diff(baseline, current, output_format="text")
    assert text_report.returncode == 1
    assert "NEW FAILURE GET /items/{id}" in text_report.stdout
    assert "at /quantity" in text_report.stdout

    resolved = _diff(current, baseline)
    assert resolved.returncode == 0
    assert json.loads(resolved.stdout)["counts"]["resolved_failure"] == 1
    resolved_text = _diff(current, baseline, output_format="text")
    assert resolved_text.returncode == 0
    assert "response.body.schema at /quantity · FAIL → PASS" in resolved_text.stdout

    legacy = json.loads(good.stdout)
    legacy["schema_version"] = "1"
    del legacy["assertions"], legacy["counts"]
    legacy_path = write_json(tmp_path / "legacy.json", legacy)
    against_legacy = _diff(legacy_path, current)
    assert against_legacy.returncode == 1
    assert json.loads(against_legacy.stdout)["compared_checks"] == 1


def test_fingerprint_ignores_operation_id_fixture_name_and_record_order(tmp_path):
    old = [_record("known", "FAIL", field="/quantity"), _record("ok", "PASS")]
    new = [
        _record("ok", "PASS", operation_id="otherId"),
        _record("known", "FAIL", field="/quantity", operation_id="newId"),
    ]
    paths = _saved_reports(tmp_path, old, new)
    result = _diff(*paths)
    assert result.returncode == 0 and not result.stderr
    data = json.loads(result.stdout)
    (finding,) = data["changes"]
    assert finding["kind"] == "existing_failure"
    assert finding["baseline"]["fingerprint"] == finding["current"]["fingerprint"]
    assert SENTINEL not in result.stdout and "newId" not in result.stdout
    assert (
        _diff(*_saved_reports(tmp_path, old[::-1], new[::-1])).stdout == result.stdout
    )


@pytest.mark.parametrize(
    ("field", "profile", "digest"),
    [
        (
            "/quantità",
            None,
            "faec54074514056e8280d9e353e0f4feb26b9311bdfb954b6928702be6734224",
        ),
        (
            "/quantità",
            "demo",
            "8b99e6d84baa69b1edd283e0f9eb877d6d48b47f5ea13e7af2014f1f184cff94",
        ),
        (
            None,
            None,
            "81a302edf423fb3f64b279ef7abb5a5f83af03927fab09b9f7708a031898d086",
        ),
        ("", None, "d4e45cd101678721f25ce9bcc3291009dde9562fd3ae1ba6493a744ae60c0fc6"),
    ],
)
def test_legacy_and_current_reports_keep_the_same_fingerprint(
    tmp_path, field, profile, digest
):
    legacy = _record("caffè", "FAIL", field=field)
    if profile:
        legacy["auth_profile"] = profile
    baseline = write_json(tmp_path / "v1.json", {"schema_version": "1", **legacy})

    updated = _record("caffè", "FAIL", field=field, operation_id="renamed")
    updated["operation"]["method"] = "get"
    updated["assertions"] = [updated.pop("assertion")]
    updated["auth_profile"] = profile
    current = write_json(
        tmp_path / "v2.json",
        {
            "schema_version": "2",
            "cases": [updated],
            "counts": {
                "ERROR": 0,
                "INCONCLUSIVE": 0,
                "FAIL": 1,
                "WARN": 0,
                "PASS": 0,
                "SKIPPED": 0,
            },
        },
    )

    for path in (baseline, current):
        report = run_cli("report", path, "--format", "markdown", offline=True)
        assert report.returncode == 0 and not report.stderr
        assert "FAIL 1" in report.stdout and "caffè" in report.stdout

    result = _diff(baseline, current)
    assert result.returncode == 0 and not result.stderr
    data = json.loads(result.stdout)
    assert data["schema_version"] == "1" and data["kind"] == "baseline_diff"
    assert data["exit_code"] == 0 and data["compared_checks"] == 1
    (change,) = data["changes"]
    assert change["kind"] == "existing_failure"
    expected = {"verdict": "FAIL", "field": field, "fingerprint": f"sha256:{digest}"}
    assert change["baseline"] == change["current"] == expected


def test_relocated_failure_has_a_new_fingerprint(tmp_path):
    paths = _saved_reports(
        tmp_path,
        [_record("example", "FAIL", field="/quantity")],
        [_record("example", "FAIL", field="/unit")],
    )
    result = _diff(*paths)
    assert result.returncode == 1
    (change,) = json.loads(result.stdout)["changes"]
    assert change["kind"] == "new_failure"
    assert change["baseline"]["fingerprint"] != change["current"]["fingerprint"]


def test_diff_does_not_match_a_case_across_auth_profiles(tmp_path):
    first = _record("same", "FAIL", source="live_http") | {"auth_profile": "alice"}
    second = first | {"auth_profile": "bob"}
    result = _diff(*_saved_reports(tmp_path, [first], [second]))
    assert result.returncode == 1
    data = json.loads(result.stdout)
    assert data["compared_checks"] == 0
    unverified, introduced = data["changes"]
    assert unverified["kind"] == "unverified"
    assert introduced["kind"] == "new_failure"
    assert unverified["baseline"]["fingerprint"] != introduced["current"]["fingerprint"]


def test_missing_and_partial_checks_do_not_resolve_baseline_findings(tmp_path):
    paths = _saved_reports(
        tmp_path,
        [
            _record("missing", "FAIL"),
            _record("skipped", "PASS"),
            _record("partial", "FAIL"),
            _record("common", "PASS"),
        ],
        [
            _record("skipped", "SKIPPED"),
            _record("partial", "INCONCLUSIVE"),
            _record("common", "PASS"),
            _record("error", "ERROR"),
        ],
    )
    result = _diff(*paths)
    assert result.returncode == 2 and not result.stderr
    data = json.loads(result.stdout)
    assert data["compared_checks"] == 1
    assert data["counts"]["resolved_failure"] == 0
    assert data["counts"]["unverified"] == 4
    missing = next(
        change for change in data["changes"] if change["check"]["case"] == "missing"
    )
    assert missing["current"] is None

    current = json.loads(paths[1].read_text())
    current["cases"].append(_record("new", "FAIL"))
    write_json(paths[1], current)
    with_regression = _diff(*paths)
    assert with_regression.returncode == 1
    assert json.loads(with_regression.stdout)["counts"]["unverified"] == 4


def test_warning_and_provenance_changes_remain_visible(tmp_path):
    paths = _saved_reports(
        tmp_path,
        [
            _record("known", "WARN"),
            _record("resolved", "WARN"),
            _record("new", "PASS"),
            _record("live", "PASS", source="live_http"),
        ],
        [
            _record("known", "WARN"),
            _record("resolved", "PASS"),
            _record("new", "WARN"),
            _record("live", "PASS"),
        ],
    )
    result = _diff(*paths)
    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["counts"]["existing_warning"] == 1
    assert data["counts"]["resolved_warning"] == 1
    assert data["counts"]["new_warning"] == 1
    assert data["counts"]["unverified"] == 1
    assert data["compared_checks"] == 3


@pytest.mark.parametrize("verdict", ["PASS", "SKIPPED"])
def test_diff_without_comparable_checks_is_incomplete(tmp_path, verdict):
    paths = _saved_reports(
        tmp_path, [_record("old", verdict)], [_record("new", verdict)]
    )
    result = _diff(*paths)
    assert result.returncode == 2
    assert json.loads(result.stdout)["compared_checks"] == 0


def test_diff_rejects_ambiguous_duplicate_checks(tmp_path):
    paths = _saved_reports(
        tmp_path,
        [_record("same", "PASS"), _record("same", "FAIL")],
        [_record("same", "FAIL")],
    )
    result = _diff(*paths)
    assert result.returncode == 2
    assert not result.stdout
    assert "ERROR [input]" in result.stderr and "duplicate assertions" in result.stderr
    assert SENTINEL not in result.stderr
