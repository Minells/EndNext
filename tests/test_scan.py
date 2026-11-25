import json
import os
import shutil
import ssl
import subprocess
import time
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from test_check import run_cli, write_json


@contextmanager
def listener(handler, tls_context=None):
    requests = []

    class Server(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Cookie"), time.monotonic()))
            status, headers, body = handler(self.path)
            with suppress(BrokenPipeError, ConnectionResetError):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Server) as server:
        if tls_context is not None:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        try:
            scheme = "http" if tls_context is None else "https"
            yield f"{scheme}://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


def config(tmp_path, origin, *, budget=1, prefix="/api", authorize=True, names=("a",)):
    path = tmp_path / "config.toml"
    settings = f"""
request_budget = {budget}
select = ["GET /api/item", "GET /outside"]

[target]
authorized = {str(authorize).lower()}
origin = {json.dumps(origin)}
path_prefix = {json.dumps(prefix)}
"""
    for name in names:
        operation = "/outside" if name == "outside" else "/api/item"
        settings += f"""
[[cases]]
name = {json.dumps(name)}
operation = "GET {operation}"
source = "http"
expected_status = 200
query_parameters = {{ q = "SCAN_QUERY_SENTINEL" }}
"""
    path.write_text(settings, encoding="utf-8")
    return path


@pytest.fixture
def contract(tmp_path):
    response = {
        "description": "Test observation",
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
    return write_json(
        tmp_path / "openapi.json",
        {
            "openapi": "3.1.1",
            "info": {"title": "Loopback fixture", "version": "1"},
            "paths": {
                path: {"get": {"responses": {"200": response}}}
                for path in ("/api/item", "/outside")
            },
        },
    )


def _json_response(path):
    return (
        200,
        {
            "Content-Type": "application/json",
            "Set-Cookie": "token=SCAN_COOKIE_SENTINEL",
        },
        b"{}",
    )


def test_scan_rate_budget_scope_cookies_and_proxy(tmp_path, contract, monkeypatch):
    with (
        listener(_json_response) as (origin, requests),
        listener(_json_response) as (proxy, proxied),
    ):
        monkeypatch.setenv("HTTP_PROXY", proxy)
        monkeypatch.setenv("HTTPS_PROXY", proxy)
        monkeypatch.setenv("ALL_PROXY", proxy)
        monkeypatch.setenv("NO_PROXY", "")
        result = run_cli(
            "scan",
            contract,
            config(tmp_path, origin, budget=2, names=("a", "b", "c", "outside")),
            "--format",
            "json",
        )
    assert result.returncode == 0, result.stderr
    assert not proxied
    assert len(requests) == 2
    assert requests[1][2] - requests[0][2] >= 0.9
    assert all(
        path == "/api/item?q=SCAN_QUERY_SENTINEL" and cookie is None
        for path, cookie, _ in requests
    )
    assert "SCAN_QUERY_SENTINEL" not in result.stdout + result.stderr
    assert "SCAN_COOKIE_SENTINEL" not in result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["requests_sent"] == data["requests_attempted"] == len(requests)
    assert [case["state"] for case in data["cases"]] == [
        "PASS",
        "PASS",
        "SKIPPED",
        "SKIPPED",
    ]
    assert data["cases"][2]["reason"] == "request_budget_exhausted"
    assert data["cases"][3]["reason"] == "outside_target_scope"


def test_scan_does_not_follow_redirect_to_another_port(tmp_path, contract):
    with listener(_json_response) as (destination, redirected):
        with listener(
            lambda _: (302, {"Location": f"{destination}/api/item"}, b"")
        ) as (origin, requests):
            result = run_cli(
                "scan", contract, config(tmp_path, origin), "--format", "json"
            )
    assert result.returncode == 2
    assert len(requests) == 1 and not redirected
    data = json.loads(result.stdout)
    assert data["requests_sent"] == len(requests)
    assert data["cases"][0]["status"] == 302


@pytest.mark.parametrize(
    "kind", ["unauthorized", "no-port", "hostname", "wrong-prefix"]
)
def test_scan_requires_authorization_port_numeric_host_and_prefix(
    tmp_path, contract, kind
):
    with listener(_json_response) as (origin, requests):
        target = {
            "no-port": "http://127.0.0.1",
            "hostname": origin.replace("127.0.0.1", "localhost"),
        }.get(kind, origin)
        params = config(
            tmp_path,
            target,
            prefix="/wrong" if kind == "wrong-prefix" else "/api",
            authorize=kind != "unauthorized",
        )
        result = run_cli("scan", contract, params, "--format", "json")
    assert result.returncode == 2
    assert not requests
    if kind == "wrong-prefix":
        assert json.loads(result.stdout)["cases"][0]["reason"] == "outside_target_scope"
    else:
        assert "ERROR [input]" in result.stderr


@pytest.mark.parametrize("mode", ["timeout", "large"])
def test_scan_bounds_response_time_and_body(tmp_path, contract, mode):
    def respond(path):
        if mode == "timeout":
            time.sleep(5.5)
            return _json_response(path)
        return 200, {"Content-Type": "application/json"}, b"x" * (1_048_576 + 1)

    with listener(respond) as (origin, requests):
        result = run_cli("scan", contract, config(tmp_path, origin), "--format", "json")
    assert result.returncode == 2
    data = json.loads(result.stdout)
    assert data["requests_sent"] == len(requests) == 1
    case = data["cases"][0]
    assert (case["state"], case["reason"]) == (
        ("ERROR", "request_timeout")
        if mode == "timeout"
        else ("INCONCLUSIVE", "response_body_limit")
    )


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("openssl"),
    reason="requires POSIX and OpenSSL",
)
def test_scan_verifies_tls_certificate(tmp_path, contract):
    key = tmp_path / "key.pem"
    cert = tmp_path / "cert.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    with listener(_json_response, context) as (origin, requests):
        result = run_cli("scan", contract, config(tmp_path, origin), "--format", "json")
    assert result.returncode == 2 and not requests
    data = json.loads(result.stdout)
    assert data["requests_sent"] == 0 and data["requests_attempted"] == 1
    assert data["cases"][0]["reason"] == "transport_error"
