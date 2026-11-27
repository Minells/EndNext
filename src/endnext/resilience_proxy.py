"""Serve one bounded HTTP/1.1 scenario on a fixed loopback route."""

import time
from collections.abc import Callable
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer

import httpx

from .inputs import InputError
from .resilience import OPERATION, Scenario, Step
from .transport import (
    REQUEST_TIMEOUT,
    Acquisition,
    LoopbackTransport,
    Target,
    _deadline,
)

_HOST = "127.0.0.1"
_DEADLINE = "run_deadline_exhausted"
type Emit = Callable[[dict[str, object]], None]


class _Handler(BaseHTTPRequestHandler):
    server: "_Proxy"
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        # Bound the header read separately so the upstream transport can own its timer.
        with ExitStack() as reading:
            self._reading = reading
            reading.enter_context(
                _deadline(max(0.001, min(REQUEST_TIMEOUT, self.server.remaining)))
            )
            try:
                super().handle()
            except OSError:
                pass

    def log_message(self, format: str, *args: object) -> None:
        pass

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        self._send(code, None, b"")

    def handle_expect_100(self) -> bool:
        self.send_error(417)
        return False

    def do_GET(self) -> None:
        self._reading.close()
        self.close_connection = True
        if self.request_version != "HTTP/1.1":
            self.send_error(505)
            return
        if self.requestline.split()[1] != OPERATION.path_template:
            self.send_error(404)
            return
        if (
            self.headers.get_all("Host", []) != [self.server.authority]
            or self.headers.get_all("Content-Length", []) not in ([], ["0"])
            or "Transfer-Encoding" in self.headers
        ):
            self.send_error(400)
            return

        reply = self.server.step()
        remaining = self.server.remaining
        if reply.status is not None and remaining > 0:
            self.connection.settimeout(min(REQUEST_TIMEOUT, remaining))
            self._send(reply.status, reply.media_type, reply.body or b"")

    def _send(self, status: int, media_type: str | None, body: bytes) -> None:
        self.close_connection = True
        if status in (204, 205, 304):
            body = b""
        self.send_response_only(status)
        self.send_header("Connection", "close")
        if media_type is not None:
            self.send_header("Content-Type", media_type)
        if status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


class _Proxy(HTTPServer):
    def __init__(
        self, port: int, scenario: Scenario, transport: LoopbackTransport, emit: Emit
    ):
        self.scenario = scenario
        self.transport = transport
        self.emit = emit
        self.attempts = 0
        self.problem: str | None = None
        self.started = time.monotonic()
        super().__init__((_HOST, port), _Handler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = _HOST
        self.server_port = self.socket.getsockname()[1]

    @property
    def authority(self) -> str:
        return f"{_HOST}:{self.server_port}"

    @property
    def remaining(self) -> float:
        return self.transport.expires - time.monotonic()

    def handle_error(self, request: object, client_address: object) -> None:
        self.problem = "listener_error"

    def run(self) -> int:
        try:
            self.emit(
                {
                    "event": "ready",
                    "origin": f"http://{self.authority}",
                    "case": self.scenario.name,
                    "scenario_digest": f"sha256:{self.scenario.digest}",
                }
            )
            while self.attempts < len(self.scenario.steps) and self.problem is None:
                self.timeout = self.remaining
                if self.timeout <= 0:
                    self.problem = _DEADLINE
                    break
                self.handle_request()
        except KeyboardInterrupt:
            self.problem = "interrupted"
        self.emit(
            {
                "event": "finished",
                "reason": self.problem or "complete",
                "attempts": self.attempts,
                "upstream_requests_sent": self.transport.sent,
            }
        )
        return 2 if self.problem is not None else 0

    def step(self) -> Acquisition:
        step = self.scenario.steps[self.attempts]
        self.attempts += 1
        reply = self._execute(step)
        self.problem = reply.problem
        self.emit(
            {
                "event": "step",
                "attempt": self.attempts,
                "outcome": step.outcome,
                "status": reply.status,
                "elapsed_ms": int((time.monotonic() - self.started) * 1000),
                "problem": reply.problem,
            }
        )
        return reply

    def _execute(self, step: Step) -> Acquisition:
        delay = step.delay_ms / 1000
        if delay >= self.remaining:
            return Acquisition(problem=_DEADLINE)
        if delay:
            time.sleep(delay)
        if self.remaining <= 0:
            return Acquisition(problem=_DEADLINE)
        if step.outcome == "forward":
            request = httpx.Request(
                "GET",
                self.transport.target.origin.copy_with(path=OPERATION.path_template),
                headers={"Accept-Encoding": "identity"},
            )
            reply = self.transport.acquire(request)
            if reply.problem is not None:
                return Acquisition(status=502, problem=reply.problem)
            if reply.status is None or not 200 <= reply.status <= 599:
                return Acquisition(status=502, problem="upstream_invalid_status")
            return reply
        if isinstance(step.outcome, int):
            return Acquisition(status=step.outcome)
        return Acquisition()


def serve_proxy(
    scenario: Scenario, *, upstream: str, authorized: bool, port: int, emit: Emit
) -> int:
    if not 0 <= port <= 65535:
        raise InputError("The proxy port must be an integer from 0 to 65535.")
    if any(step.outcome == "timeout" and not step.delay_ms for step in scenario.steps):
        raise InputError("A proxy timeout requires a positive delay_ms.")
    try:
        target = Target.load(
            {
                "origin": upstream,
                "path_prefix": OPERATION.path_template,
                "authorized": authorized,
            }
        )
    except InputError:
        raise InputError(
            "The proxy requires --authorized and a literal HTTP(S) loopback origin with an explicit port."
        ) from None
    transport = LoopbackTransport(target, len(scenario.steps))
    try:
        with _Proxy(port, scenario, transport, emit) as proxy:
            if target.origin == httpx.URL(
                f"{target.origin.scheme}://{proxy.authority}"
            ):
                raise InputError(
                    "The proxy cannot use its own listener as the upstream."
                )
            return proxy.run()
    finally:
        transport.close()
