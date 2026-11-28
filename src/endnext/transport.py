"""Synchronous, explicitly scoped loopback transport for GET cases."""

import signal
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import current_thread, main_thread

import httpx

from .inputs import MAX_BYTES, MEDIA_TYPE, InputError
from .policy import MAX_REQUESTS, Target

REQUEST_TIMEOUT = 5.0
RUN_TIMEOUT = 60.0
REQUEST_INTERVAL = 1.0


@dataclass(frozen=True)
class Acquisition:
    status: int | None = None
    media_type: str | None = None
    body: bytes | None = None
    problem: str | None = None


class _Deadline(TimeoutError):
    pass


@contextmanager
def _deadline(seconds: float) -> Iterator[None]:
    def interrupt(signum: int, frame: object) -> None:
        raise _Deadline

    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise InputError("Live transport requires an unused process timer.")
    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class LoopbackTransport:
    def __init__(self, target: Target, budget: int):
        if not hasattr(signal, "setitimer") or current_thread() is not main_thread():
            raise InputError("Scan requires a POSIX main process for its hard timeout.")
        self.target = target
        self.budget = min(budget, MAX_REQUESTS)
        self.attempted = 0
        self.sent = 0
        self.expires = time.monotonic() + RUN_TIMEOUT
        self.next_attempt = 0.0
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(retries=0, verify=True, trust_env=False),
            trust_env=False,
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

    def close(self) -> None:
        self.client.close()

    def _trace(self, event: str, info: Mapping[str, object]) -> None:
        if event == "http11.send_request_headers.complete":
            self.sent += 1

    def acquire(
        self, request: httpx.Request, *, bearer_token: str | None = None
    ) -> Acquisition:
        if self.attempted >= self.budget:
            return Acquisition(problem="request_budget_exhausted")
        now = time.monotonic()
        if max(now, self.next_attempt) >= self.expires:
            return Acquisition(problem="run_deadline_exhausted")
        if self.next_attempt > now:
            time.sleep(self.next_attempt - now)
        now = time.monotonic()
        if now >= self.expires:
            return Acquisition(problem="run_deadline_exhausted")
        if not self.target.permits_request(request):
            return Acquisition(problem="outside_target_scope")

        self.attempted += 1
        self.next_attempt = now + REQUEST_INTERVAL
        request.extensions["trace"] = self._trace
        status = None
        media_type = None
        try:
            if bearer_token is not None:
                request.headers["Authorization"] = f"Bearer {bearer_token}"
            with _deadline(min(REQUEST_TIMEOUT, self.expires - now)):
                response = self.client.send(
                    request, stream=True, follow_redirects=False
                )
                try:
                    status = response.status_code
                    mime = (
                        response.headers.get("Content-Type", "")
                        .partition(";")[0]
                        .strip()
                        .lower()
                    )
                    media_type = mime if MEDIA_TYPE.fullmatch(mime) else None
                    encoding = (
                        response.headers.get("Content-Encoding", "identity")
                        .strip()
                        .lower()
                    )
                    metadata_only = request.method == "HEAD" or status == 304
                    if not metadata_only and encoding != "identity":
                        return Acquisition(
                            status, media_type, problem="unsupported_content_encoding"
                        )
                    length = response.headers.get("Content-Length", "")
                    if (
                        not metadata_only
                        and length.isascii()
                        and length.isdecimal()
                        and (len(length) > 10 or int(length) > MAX_BYTES)
                    ):
                        return Acquisition(
                            status, media_type, problem="response_body_limit"
                        )
                    body = bytearray()
                    for chunk in response.iter_raw():
                        if len(body) + len(chunk) > MAX_BYTES:
                            return Acquisition(
                                status, media_type, problem="response_body_limit"
                            )
                        body.extend(chunk)
                    return Acquisition(status, media_type, bytes(body))
                finally:
                    response.close()
        except (_Deadline, httpx.TimeoutException):
            return Acquisition(status, media_type, problem="request_timeout")
        except httpx.HTTPError:
            return Acquisition(status, media_type, problem="transport_error")
        finally:
            if bearer_token is not None:
                request.headers.pop("Authorization", None)
            self.client.cookies.clear()
