import json
import subprocess
import sys

import pytest
from test_check import CONTRACT, EXAMPLES, FAIL, PASS, SENTINEL, cli, write_json

from endnext import api_v1


def test_python_example_matches_installed_cli_evidence(tmp_path):
    partial = json.loads(PASS.read_text())
    del partial["body"]
    partial["body_state"] = "truncated"
    fixtures = (PASS, FAIL, write_json(tmp_path / "partial.json", partial))
    batch = subprocess.run(
        [
            sys.executable,
            str(EXAMPLES / "check_fixtures.py"),
            str(CONTRACT),
            *map(str, fixtures),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert batch.returncode == 2 and not batch.stderr
    assert SENTINEL not in batch.stdout
    reports = [json.loads(line) for line in batch.stdout.splitlines()]
    for fixture, expected_exit, report in zip(
        fixtures, (0, 1, 2), reports, strict=True
    ):
        single = cli(fixture=fixture, offline=True)
        assert single.returncode == expected_exit and not single.stderr
        expected = json.loads(single.stdout)
        assert report.pop("run_id") != expected.pop("run_id")
        assert report == expected


def test_python_api_sanitizes_unexpected_errors(monkeypatch):
    checker = api_v1.FixtureChecker(str(CONTRACT))

    def fail(*args, **kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(api_v1, "verify", fail)
    with pytest.raises(api_v1.ExecutionError, match="could not complete") as failure:
        checker.check(PASS)
    assert SENTINEL not in str(failure.value)
    assert failure.value.__suppress_context__


def test_isolated_checker_recovers_after_deadline(monkeypatch):
    checker = api_v1.IsolatedFixtureChecker(CONTRACT)
    with monkeypatch.context() as patch:
        patch.setattr(api_v1, "CONTRACT_TIMEOUT", 0.001)
        with pytest.raises(api_v1.ExecutionError, match="timed out"):
            checker.check(PASS)
    assert checker.check(PASS)["verdict"] == "PASS"
    with pytest.raises(api_v1.InputError):
        checker.check(CONTRACT)
