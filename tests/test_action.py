import json
import os
import subprocess

import pytest
import yaml
from test_check import ENTRY_POINT, EXAMPLES
from test_diff import _diff, _exception_policy, _record, _saved_reports

METADATA = EXAMPLES.parent / "action.yml"


@pytest.mark.parametrize(
    ("scenario", "code", "kind"),
    [
        ("regression", 1, "new_failure"),
        ("existing", 0, "existing_failure"),
        ("incomplete", 2, "unverified"),
        ("invalid", 2, None),
        ("excepted", 0, "new_failure"),
    ],
)
def test_composite_action_collects_evidence_before_applying_gate(
    tmp_path, scenario, code, kind
):
    action = yaml.safe_load(METADATA.read_text())
    steps = action["runs"]["steps"]
    comparison = next(step for step in steps if step.get("id") == "compare")
    upload = next(step for step in steps if step.get("id") == "artifact")
    assert steps.index(comparison) < steps.index(upload) < len(steps) - 1

    report = _record("document", "FAIL", field="/quantity")
    before = report if scenario == "existing" else _record("document", "PASS")
    current_record = (
        _record("document", "INCONCLUSIVE") if scenario == "incomplete" else report
    )
    baseline, current = _saved_reports(tmp_path, [before], [current_record])
    if scenario == "invalid":
        current.write_text("invalid JSON")
    policy_path = ""
    if scenario == "excepted":
        (finding,) = json.loads(_diff(baseline, current).stdout)["changes"]
        policy_path = str(
            _exception_policy(
                tmp_path / "exceptions with spaces.toml",
                finding["current"]["fingerprint"],
                "9999-12-31",
            )
        )

    baseline = baseline.rename(tmp_path / "baseline $(touch injected).json")
    work = tmp_path / "runner temp"
    evidence = work / "evidence"
    evidence.mkdir(parents=True)
    executable = work / "venv" / "bin" / "endnext"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(ENTRY_POINT)
    output = tmp_path / "output.txt"
    environment = os.environ | {
        "ENDNEXT_BASELINE": baseline.name,
        "ENDNEXT_CURRENT": current.name,
        "ENDNEXT_EXCEPTIONS": policy_path,
        "ENDNEXT_WORK_DIR": str(work),
        "GITHUB_OUTPUT": str(output),
    }

    def run_bash(step, env):
        return subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-e",
                "-o",
                "pipefail",
                "-c",
                step["run"],
            ],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    collected = run_bash(comparison, environment)
    assert collected.returncode == 0, collected.stderr
    assert not (tmp_path / "injected").exists()
    assert output.read_text() == f"exit-code={code}\n"
    if kind is None:
        assert [file.name for file in evidence.iterdir()] == ["diagnostics.txt"]
        assert "ERROR [input]" in (evidence / "diagnostics.txt").read_text()
    else:
        assert [file.name for file in evidence.iterdir()] == ["diff.json"]
        result = json.loads((evidence / "diff.json").read_text())
        assert result["exit_code"] == code
        assert result["counts"][kind] == 1
    applied = run_bash(steps[-1], environment | {"ENDNEXT_EXIT_CODE": str(code)})
    assert applied.returncode == code
