"""Synchronous, explicitly scoped loopback transport for GET cases."""

import ipaddress
import signal
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from threading import current_thread, main_thread
from urllib.parse import urlsplit

import httpx

from .inputs import MAX_BYTES, MEDIA_TYPE, PATH_PARAMETER, InputError
from .planner import MAX_REQUESTS, Case

REQUEST_TIMEOUT = 5.0
RUN_TIMEOUT = 60.0
REQUEST_INTERVAL = 1.0


def _safe_path(path: str) -> bool:
    return (
        path.startswith("/")
        and "//" not in path
        and all(char.isprintable() and char not in "%\\?#" for char in path)
        and not {".", ".."}.intersection(path.split("/"))
    )


@dataclass(frozen=True)
class Target:
    origin: httpx.URL
    path_prefix: str

    @classmethod
    def load(cls, raw: object) -> "Target":
        if not isinstance(raw, dict) or raw.get("authorized") is not True:
            raise InputError(
                "Scan requires an explicit target authorization declaration."
            )
        address = raw.get("origin")
        prefix = raw.get("path_prefix")
        if (
            not isinstance(address, str)
            or not isinstance(prefix, str)
            or not _safe_path(prefix)
        ):
            raise InputError(
                "Scan requires a loopback origin and an absolute path prefix."
            )
        try:
            # HTTPX canonicalizes default ports away. Require explicit port in the
            # configured authority before constructing the canonical URL.
            parts = urlsplit(address)
            port = parts.port
            host = parts.hostname or ""
            literal = ipaddress.ip_address(host)
        except ValueError:
            raise InputError(
                "Scan requires a literal loopback IP and an explicit port."
            ) from None
        if (
            not literal.is_loopback
            or "%" in host
            or port is None
            or port == 0
            or parts.scheme not in {"http", "https"}
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or "?" in address
            or "#" in address
            or any(not char.isprintable() or char.isspace() for char in address)
        ):
            raise InputError(
                "Scan requires an HTTP(S) loopback origin with an explicit port."
            )
        authority = f"[{literal}]" if literal.version == 6 else str(literal)
        return cls(
            httpx.URL(f"{parts.scheme}://{authority}:{port}"), prefix.rstrip("/") or "/"
        )

    def permits(self, url: httpx.URL) -> bool:
        path = url.path
        return (
            (url.scheme, url.host, url.port)
            == (self.origin.scheme, self.origin.host, self.origin.port)
            and not url.userinfo
            and not url.fragment
            and _safe_path(path)
            and (
                self.path_prefix == "/"
                or path == self.path_prefix
                or path.startswith(self.path_prefix + "/")
            )
        )

    def request(self, case: Case) -> httpx.Request | None:
        method, template = case.operation
        values = {name: str(value) for name, value in case.path_parameters.items()}
        if method != "GET" or any("/" in value for value in values.values()):
            return None
        path = PATH_PARAMETER.sub(lambda match: values[match[1]], template)
        if not _safe_path(path):
            return None
        request = httpx.Request(
            "GET",
            self.origin.copy_with(path=path),
            params=case.query_parameters,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        )
        return request if self.permits(request.url) else None


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
        if (
            request.method != "GET"
            or not self.target.permits(request.url)
            or request.headers.get_list("host") != [request.url.netloc.decode("ascii")]
            or "cookie" in request.headers
            or "authorization" in request.headers
        ):
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
                    if encoding != "identity":
                        return Acquisition(
                            status, media_type, problem="unsupported_content_encoding"
                        )
                    length = response.headers.get("Content-Length", "")
                    if (
                        length.isascii()
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
