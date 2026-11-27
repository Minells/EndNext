import json
import signal
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from select import select

import httpx
import pytest
from test_check import ENTRY_POINT, EXAMPLES, SENTINEL, run_cli
from test_scan import listener

from endnext import transport
from endnext.inputs import InputError
from endnext.resilience import Scenario, run_scenario
from endnext.resilience_proxy import serve_proxy

SCENARIO = EXAMPLES / "resilience-proxy.toml"


def _scenario(tmp_path, steps):
    header = (
        SCENARIO.read_text()
        .split("[[steps]]", 1)[0]
        .replace("min_attempts = 3", "min_attempts = 1")
    )
    path = tmp_path / "scenario.toml"
    path.write_text(header + steps)
    return path


@contextmanager
def _start(upstream, scenario=SCENARIO):
    with subprocess.Popen(
        [
            str(ENTRY_POINT),
            "resilience-proxy",
            str(scenario),
            "--authorized",
            "--upstream",
            upstream,
            "--port",
            "0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert select([process.stdout], [], [], 20)[0], (
                "Proxy did not publish readiness"
            )
            first = json.loads(process.stdout.readline())
            assert first["event"] == "ready"
            assert first["origin"].startswith("http://127.0.0.1:")
            assert first["scenario_digest"].startswith("sha256:")
            yield process, first["origin"] + "/dependency"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def _finish(process, code):
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == code, stderr
    assert not stderr
    assert SENTINEL not in stdout
    return [json.loads(line) for line in stdout.splitlines()]


def test_http11_sequence_forwards_only_the_fixed_request(tmp_path, monkeypatch):
    headers = []
    with (
        listener(lambda _: (200, {}, b"")) as (ambient, proxied),
        listener(
            lambda _: (
                200,
                {"Content-Type": "text/plain", "Set-Cookie": f"private={SENTINEL}"},
                SENTINEL.encode(),
            ),
            headers_seen=headers,
        ) as (upstream, received),
    ):
        monkeypatch.setenv("HTTP_PROXY", ambient)
        monkeypatch.setenv("ALL_PROXY", ambient)
        monkeypatch.setenv("NO_PROXY", "")
        with (
            _start(upstream) as (process, url),
            httpx.Client(trust_env=False) as client,
        ):
            assert client.get(url + "?q=" + SENTINEL).status_code == 404
            assert (
                client.get(url, headers={"Host": "example.invalid"}).status_code == 400
            )
            assert client.post(url).status_code == 501
            assert client.request("GET", url, content=b"body").status_code == 400
            assert (
                client.request(
                    "GET", url, content=b"body", headers={"Expect": "100-continue"}
                ).status_code
                == 417
            )
            assert client.get(url).status_code == 503
            assert client.get(url).status_code == 429
            assert not received
            final = client.get(
                url,
                headers={
                    "Authorization": f"Bearer {SENTINEL}",
                    "Cookie": f"token={SENTINEL}",
                },
            )
            assert final.status_code == 200 and final.http_version == "HTTP/1.1"
            assert (
                final.text == SENTINEL and final.headers["Content-Type"] == "text/plain"
            )
            assert "set-cookie" not in final.headers
            first, second, third, finished = _finish(process, 0)
    assert not proxied
    assert [(path, cookie) for path, cookie, _ in received] == [("/dependency", None)]
    assert {name.lower() for name in headers[0]} == {"host", "accept-encoding"}
    assert [event["outcome"] for event in (first, second, third)] == [
        503,
        429,
        "forward",
    ]
    assert [event["status"] for event in (first, second, third)] == [503, 429, 200]
    assert first["elapsed_ms"] >= 70
    assert second["elapsed_ms"] - first["elapsed_ms"] >= 95
    assert finished == {
        "event": "finished",
        "reason": "complete",
        "attempts": 3,
        "upstream_requests_sent": 1,
    }


def test_timeout_and_disconnect_use_real_connections(tmp_path):
    scenario = _scenario(
        tmp_path,
        """
[[steps]]
outcome = "timeout"
delay_ms = 350
[[steps]]
outcome = "disconnect"
[[steps]]
outcome = 204
""",
    )
    with listener(lambda _: (200, {}, b"")) as (upstream, received):
        with (
            _start(upstream, scenario) as (process, url),
            httpx.Client(trust_env=False, timeout=3) as client,
        ):
            with pytest.raises(httpx.ReadTimeout):
                client.get(url, timeout=0.1)
            with pytest.raises((httpx.RemoteProtocolError, httpx.ReadError)):
                client.get(url)
            final = client.get(url)
            assert final.status_code == 204 and "content-length" not in final.headers
            timeout, disconnect, complete, finished = _finish(process, 0)
    assert not received
    assert timeout["outcome"] == "timeout" and timeout["status"] is None
    assert timeout["elapsed_ms"] >= 340
    assert disconnect["outcome"] == "disconnect" and disconnect["status"] is None
    assert complete["status"] == 204
    assert finished["upstream_requests_sent"] == 0


@pytest.mark.parametrize(
    ("status", "length", "client_status", "problem"),
    [
        (302, None, 302, None),
        (200, "1048577", 502, "response_body_limit"),
        (600, None, 502, "upstream_invalid_status"),
    ],
)
def test_forward_respects_redirect_and_response_limits(
    tmp_path, status, length, client_status, problem
):
    scenario = _scenario(tmp_path, '[[steps]]\noutcome = "forward"\n')
    with listener(lambda _: (200, {}, b"")) as (destination, redirected):
        headers = {"Location": f"{destination}/{SENTINEL}"}
        if length is not None:
            headers["Content-Length"] = length
        with listener(lambda _: (status, headers, b"")) as (upstream, received):
            with (
                _start(upstream, scenario) as (process, url),
                httpx.Client(trust_env=False, follow_redirects=True) as client,
            ):
                result = client.get(url)
                assert (
                    result.status_code == client_status
                    and "location" not in result.headers
                )
                forwarded, finished = _finish(process, 0 if problem is None else 2)
    assert not redirected and len(received) == 1
    assert forwarded["status"] == client_status and forwarded["problem"] == problem
    assert finished["reason"] == (problem or "complete")


@pytest.mark.parametrize(
    "options",
    [
        ("--upstream", "http://127.0.0.1:8767"),
        ("--upstream", "http://example.invalid:8767", "--authorized"),
        ("--upstream", "http://127.0.0.1", "--authorized"),
        ("--upstream", "http://127.0.0.1:8767/elsewhere", "--authorized"),
        ("--upstream", "http://127.0.0.1:8767", "--authorized", "--port", "-1"),
    ],
)
def test_invalid_proxy_scope_is_rejected_offline(options):
    result = run_cli("resilience-proxy", SCENARIO, *options, offline=True)
    assert result.returncode == 2 and not result.stdout
    assert "ERROR [input]" in result.stderr


def test_proxy_requires_positive_timeout_delay(tmp_path):
    scenario = _scenario(tmp_path, '[[steps]]\noutcome = "timeout"\n')
    result = run_cli(
        "resilience-proxy",
        scenario,
        "--upstream",
        "http://127.0.0.1:8767",
        "--authorized",
        offline=True,
    )
    assert result.returncode == 2 and not result.stdout
    assert "positive delay_ms" in result.stderr


def test_forward_is_explicitly_live_only():
    result = run_cli("resilience", SCENARIO, offline=True)
    assert result.returncode == 2 and not result.stdout
    assert "requires the live resilience proxy" in result.stderr
    scenario = Scenario.load(SCENARIO, allow_forward=True)
    scenario = replace(scenario, steps=scenario.steps[-1:])
    run = run_scenario(scenario, lambda session: session.get("/dependency").status_code)
    assert run.exit_code == 2 and not run.events


def test_proxy_rejects_its_own_listener():
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    scenario = Scenario.load(SCENARIO, allow_forward=True)
    with pytest.raises(InputError, match="own listener"):
        serve_proxy(
            scenario,
            upstream=f"https://127.0.0.1:{port}",
            authorized=True,
            port=port,
            emit=[].append,
        )


def test_proxy_exits_at_deadline_without_an_attempt(monkeypatch):
    monkeypatch.setattr(transport, "RUN_TIMEOUT", 0.05)
    scenario = Scenario.load(SCENARIO, allow_forward=True)
    events = []
    assert (
        serve_proxy(
            scenario,
            upstream="http://127.0.0.1:8767",
            authorized=True,
            port=0,
            emit=events.append,
        )
        == 2
    )
    assert events[-1]["reason"] == "run_deadline_exhausted"
    assert events[-1]["attempts"] == 0


def test_proxy_emits_incomplete_summary_on_interrupt():
    with _start("http://127.0.0.1:8767") as (process, _):
        process.send_signal(signal.SIGINT)
        (finished,) = _finish(process, 2)
    assert finished["reason"] == "interrupted" and finished["attempts"] == 0
