import json
import subprocess
import sys
from contextlib import contextmanager
from select import select

import pytest
from test_check import EXAMPLES, run_cli
from test_scan import listener

from endnext.inputs import InputError
from endnext.planner import PlanConfig

CONTRACT = EXAMPLES / "authorization.openapi.json"
SETTINGS = (EXAMPLES / "authorization-plan.toml").read_text(encoding="utf-8")
ALICE = "endnext-credential-sentinel.alice"
BOB = "endnext-credential-sentinel.bob"
RULE = "security.authenticated_access"


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("ENDNEXT_DEMO_ALICE_TOKEN", ALICE)
    monkeypatch.setenv("ENDNEXT_DEMO_BOB_TOKEN", BOB)


@contextmanager
def demo(*args):
    with subprocess.Popen(
        [sys.executable, str(EXAMPLES / "authorization_demo.py"), "--port", "0", *args],
        stdout=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert select([process.stdout], [], [], 5)[0], "Demo did not start"
            origin = process.stdout.readline().strip()
            assert origin.startswith("http://127.0.0.1:")
            yield origin
        finally:
            process.terminate()
            process.wait(timeout=5)


def config(tmp_path, origin, settings=SETTINGS):
    plan_path = tmp_path / "authorization.toml"
    plan_path.write_text(settings.replace("http://127.0.0.1:8765", origin))
    return plan_path


def scan(tmp_path, origin, *, output="json", single=False):
    settings = SETTINGS
    if single:
        settings = SETTINGS.split('[[cases]]\nname = "bob-other-document"')[0]
    return run_cli(
        "scan", CONTRACT, config(tmp_path, origin, settings), "--format", output
    )


def assert_redacted(result):
    for token in (ALICE, BOB):
        assert token not in result.stdout
        assert token not in result.stderr


def test_auth_plan_stays_offline_and_independent_of_environment(monkeypatch):
    monkeypatch.delenv("ENDNEXT_DEMO_ALICE_TOKEN", raising=False)
    monkeypatch.delenv("ENDNEXT_DEMO_BOB_TOKEN", raising=False)
    path = EXAMPLES / "authorization-plan.toml"
    without_tokens = run_cli("plan", CONTRACT, path, "--format", "json", offline=True)
    monkeypatch.setenv("ENDNEXT_DEMO_ALICE_TOKEN", ALICE)
    monkeypatch.setenv("ENDNEXT_DEMO_BOB_TOKEN", BOB)
    with_tokens = run_cli("plan", CONTRACT, path, "--format", "json", offline=True)
    assert without_tokens.returncode == with_tokens.returncode == 0
    assert without_tokens.stdout == with_tokens.stdout and not with_tokens.stderr
    assert_redacted(with_tokens)
    data = json.loads(with_tokens.stdout)
    assert data["requests_planned"] == data["candidate_assertions"] == 3
    (operation,) = data["operations"]
    assert not operation["allows_anonymous"]
    assert [
        (case["auth_profile"], case["expected_access"]) for case in operation["cases"]
    ] == [("alice", "allow"), ("bob", "deny"), ("bob", "allow")]
    text = run_cli("plan", CONTRACT, path, offline=True)
    assert "bob expects deny" in text.stdout
    assert_redacted(text)


def test_installed_cli_demo_reports_and_diffs_access_results(
    tmp_path, credentials, monkeypatch
):
    with demo("--healthy") as origin:
        baseline = scan(tmp_path, origin)
    with demo() as origin:
        current = scan(tmp_path, origin)
        monkeypatch.delenv("ENDNEXT_DEMO_BOB_TOKEN")
        skipped = scan(tmp_path, origin)
    assert (baseline.returncode, current.returncode, skipped.returncode) == (0, 1, 2)
    paths = {}
    for label, result in (
        ("baseline", baseline),
        ("current", current),
        ("skipped", skipped),
    ):
        assert not result.stderr
        assert_redacted(result)
        paths[label] = tmp_path / f"{label}.json"
        paths[label].write_text(result.stdout)
    data = json.loads(current.stdout)
    assert data["requests_sent"] == data["requests_attempted"] == 3
    assert [
        (case["auth_profile"], case["status"], case["state"]) for case in data["cases"]
    ] == [("alice", 204, "PASS"), ("bob", 403, "PASS"), ("bob", 403, "FAIL")]
    partial = json.loads(skipped.stdout)
    assert partial["requests_sent"] == partial["requests_attempted"] == 1
    assert [case["reason"] for case in partial["cases"][1:]] == [
        "auth_credential_missing"
    ] * 2
    assert partial["counts"]["SKIPPED"] == 2

    markdown = run_cli("report", paths["current"], "--format", "markdown", offline=True)
    assert markdown.returncode == 0
    assert (
        r"live\_http \(bob\) | FAIL | security.authenticated\_access" in markdown.stdout
    )
    assert "Expected allow. The service returned HTTP 403." in markdown.stdout
    assert_redacted(markdown)
    diff = run_cli(
        "diff", paths["baseline"], paths["current"], "--format", "json", offline=True
    )
    assert diff.returncode == 1
    (change,) = json.loads(diff.stdout)["changes"]
    assert change["kind"] == "new_failure"
    assert change["check"]["auth_profile"] == "bob"
    assert change["check"]["rule"] == RULE
    assert_redacted(diff)
    incomplete = run_cli("diff", paths["current"], paths["skipped"], offline=True)
    assert incomplete.returncode == 2
    assert "0 resolved failure" in incomplete.stdout
    assert "2 unverified" in incomplete.stdout
    assert "[live_http, bob]" in incomplete.stdout
    assert_redacted(incomplete)


@pytest.mark.parametrize(
    ("status", "body_issue", "verdict", "exit_code", "reason"),
    [
        (403, None, "FAIL", 1, "Expected allow. The service returned HTTP 403."),
        (
            401,
            None,
            "INCONCLUSIVE",
            2,
            "HTTP 401 does not establish an authenticated access outcome.",
        ),
        (403, "short", "ERROR", 2, "transport_error"),
        (403, "large", "INCONCLUSIVE", 2, "response_body_limit"),
    ],
)
def test_reflected_credential_stays_out_of_auth_evidence(
    tmp_path, credentials, status, body_issue, verdict, exit_code, reason
):
    body = json.dumps({"token": ALICE}).encode()
    headers = {"Content-Type": f"application/{ALICE}", "X-Reflected-Credential": ALICE}
    if body_issue == "short":
        headers["Content-Length"] = str(len(body) + 1)
    elif body_issue == "large":
        headers["Content-Length"] = "1048577"
    with listener(lambda _: (status, headers, body)) as (origin, received):
        result = scan(tmp_path, origin, single=True)
    assert result.returncode == exit_code and not result.stderr
    assert len(received) == 1
    assert_redacted(result)
    (case,) = json.loads(result.stdout)["cases"]
    assert (case["state"], case["status"], case["media_type"]) == (
        verdict,
        status,
        None,
    )
    assert case["assertion"]["rule"] == RULE
    assert case["reason"] == reason
    path = tmp_path / "evidence.json"
    path.write_text(result.stdout)
    rendered = run_cli("report", path, offline=True)
    assert rendered.returncode == 0 and verdict in rendered.stdout
    assert_redacted(rendered)


def test_invalid_token_is_not_sent_or_printed(tmp_path, credentials, monkeypatch):
    monkeypatch.setenv("ENDNEXT_DEMO_ALICE_TOKEN", f"{ALICE}\r\nCookie: injected")
    with listener(lambda _: (204, {}, b"")) as (origin, received):
        result = scan(tmp_path, origin, output="text", single=True)
    assert result.returncode == 2 and not result.stderr
    assert not received
    assert "alice-own-document [alice] · auth_credential_invalid" in result.stdout
    assert_redacted(result)


def test_auth_requests_respect_redirect_and_scope_limits(tmp_path, credentials):
    with listener(lambda _: (204, {}, b"")) as (other_origin, forwarded):
        with listener(
            lambda _: (302, {"Location": f"{other_origin}/documents/{ALICE}"}, b"")
        ) as (origin, received):
            result = scan(tmp_path, origin, single=True)
            outside = run_cli(
                "scan",
                CONTRACT,
                config(
                    tmp_path,
                    origin,
                    SETTINGS.replace(
                        'path_prefix = "/documents"', 'path_prefix = "/other"'
                    ),
                ),
                "--format",
                "json",
            )
    assert result.returncode == outside.returncode == 2
    assert not forwarded and len(received) == 1
    assert json.loads(result.stdout)["cases"][0]["state"] == "INCONCLUSIVE"
    assert json.loads(outside.stdout)["cases"][0]["reason"] == "outside_target_scope"
    assert_redacted(result)
    assert_redacted(outside)


@pytest.mark.parametrize(
    "settings",
    [
        "select = []\nauth_profiles = []",
        'select = []\n[auth_profiles."bad name"]\nbearer_env = "TEST_ENV"',
        'select = []\n[auth_profiles.alice]\nbearer_env = "not a variable"',
        SETTINGS.replace(
            'bearer_env = "ENDNEXT_DEMO_ALICE_TOKEN"', f'token = "{ALICE}"'
        ),
        SETTINGS.replace('auth_profile = "bob"', 'auth_profile = "undefined"'),
        SETTINGS.replace('expected_access = "deny"', "expected_status = 403"),
        SETTINGS.replace('expected_access = "deny"', 'expected_access = "maybe"'),
        SETTINGS.replace('auth_profile = "bob"', ""),
        SETTINGS.replace('source = "http"', 'source = "fixture"'),
    ],
)
def test_rejects_invalid_profile_configuration(tmp_path, settings):
    with pytest.raises(InputError) as error:
        PlanConfig.load(config(tmp_path, "http://127.0.0.1:8765", settings))
    assert ALICE not in str(error.value)
