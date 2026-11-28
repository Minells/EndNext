from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from endnext import transport
from endnext.inputs import InputError
from endnext.planner import Case
from endnext.policy import Target
from endnext.transport import Acquisition, LoopbackTransport


@pytest.fixture
def target():
    return Target.load(
        {
            "authorized": True,
            "origin": "http://127.0.0.1:8080",
            "path_prefix": "/api",
        }
    )


@pytest.fixture
def client(target):
    client = LoopbackTransport(target, budget=1)
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize(
    ("origin", "prefix"),
    [
        ("http://127.0.0.1:8080", "api"),
        ("http://127.0.0.1:8080", "/api//items"),
        ("http://127.0.0.1:8080", "/api/../items"),
        ("http://127.0.0.1:0", "/"),
        ("http://127.0.0.1:65536", "/"),
        ("http://192.0.2.44:8080", "/"),
        ("ftp://127.0.0.1:8080", "/"),
        ("http://user@127.0.0.1:8080", "/"),
        ("http://127.0.0.1:8080/api", "/"),
        ("http://127.0.0.1:8080/?q=1", "/"),
        ("http://127.0.0.1:8080/#fragment", "/"),
        ("http://127.0.0.1:8080\n", "/"),
    ],
)
def test_target_rejects_incomplete_or_out_of_scope_origins(origin, prefix):
    with pytest.raises(InputError, match="Scan requires"):
        Target.load({"authorized": True, "origin": origin, "path_prefix": prefix})


def test_target_accepts_explicit_ipv6_default_port():
    target = Target.load(
        {
            "authorized": True,
            "origin": "http://[::1]:80",
            "path_prefix": "/",
        }
    )
    assert target.permits(httpx.URL("http://[::1]/api"))
    assert not target.permits(httpx.URL("http://[::1]:8080/api"))


def test_target_prepares_path_and_query_and_checks_prefix_boundaries(target):
    case = Case(
        "single",
        ("GET", "/api/{id}"),
        "http",
        200,
        None,
        {"id": "blue widget"},
        {"q": "a & b"},
    )
    request = target.request(case)
    assert request is not None
    assert request.url.raw_path == b"/api/blue%20widget?q=a+%26+b"
    assert request.headers["Accept-Encoding"] == "identity"
    assert target.permits(httpx.URL("http://127.0.0.1:8080/api"))
    assert not target.permits(httpx.URL("http://127.0.0.1:8080/apiary"))
    assert target.request(replace(case, operation=("POST", "/api/{id}"))) is None
    assert target.request(replace(case, path_parameters={"id": "two/segments"})) is None
    assert target.request(replace(case, path_parameters={"id": ".."})) is None


@pytest.mark.parametrize(
    ("method", "url", "headers"),
    [
        ("POST", "http://127.0.0.1:8080/api", {}),
        ("GET", "http://127.0.0.1:8081/api", {}),
        ("GET", "http://127.0.0.1:8080/apiary", {}),
        ("GET", "http://127.0.0.1:8080/api", {"Host": "other.invalid"}),
        ("GET", "http://127.0.0.1:8080/api", {"Cookie": "id=now"}),
        ("GET", "http://127.0.0.1:8080/api", {"Authorization": "Bearer sample"}),
        ("GET", "http://127.0.0.1:8080/api", {"Transfer-Encoding": "chunked"}),
        ("GET", "http://127.0.0.1:8080/api", {"Content-Length": "5"}),
    ],
)
def test_last_dispatch_check_keeps_out_of_scope_requests_off_the_transport(
    client,
    monkeypatch,
    method,
    url,
    headers,
):
    def must_not_send(*args, **kwargs):
        pytest.fail("Rejected requests must never reach HTTPX")

    monkeypatch.setattr(client.client, "send", must_not_send)
    result = client.acquire(
        httpx.Request(method, url, headers=headers), bearer_token="test-sentinel"
    )
    assert result == Acquisition(problem="outside_target_scope")
    assert client.attempted == client.sent == 0


@pytest.mark.parametrize("streaming", [False, True])
def test_transport_rejects_buffered_and_unread_request_bodies(
    client, monkeypatch, streaming
):
    def must_not_send(*args, **kwargs):
        pytest.fail("Body-bearing requests must never reach HTTPX")

    def unread_body():
        pytest.fail("The transport must reject a request stream without reading it")
        yield b"unexpected"

    monkeypatch.setattr(client.client, "send", must_not_send)
    request = httpx.Request(
        "GET",
        "http://127.0.0.1:8080/api",
        headers={"Host": "127.0.0.1:8080", "Content-Length": "0"},
        content=unread_body() if streaming else b"unexpected",
    )
    assert client.acquire(request) == Acquisition(problem="outside_target_scope")
    assert client.attempted == client.sent == 0


@pytest.mark.parametrize(
    "case", ["budget", "expired", "would_miss_deadline", "late_wakeup"]
)
def test_run_stops_before_dispatch_when_budget_or_time_is_exhausted(
    client,
    monkeypatch,
    case,
):
    def must_not_send(*args, **kwargs):
        pytest.fail("Expired requests must never reach HTTPX")

    monkeypatch.setattr(client.client, "send", must_not_send)
    times = {
        "budget": [],
        "expired": [12],
        "would_miss_deadline": [0],
        "late_wakeup": [0, 12],
    }[case]
    sleeps = []
    clock = SimpleNamespace(monotonic=iter(times).__next__, sleep=sleeps.append)
    monkeypatch.setattr(transport, "time", clock)
    client.expires = 10
    client.next_attempt = 10 if case == "would_miss_deadline" else 3
    if case == "budget":
        client.budget = 0
    result = client.acquire(httpx.Request("GET", "http://127.0.0.1:8080/api"))
    expected = (
        "request_budget_exhausted" if case == "budget" else "run_deadline_exhausted"
    )
    assert result == Acquisition(problem=expected)
    assert client.attempted == client.sent == 0
    assert sleeps == ([3] if case == "late_wakeup" else [])


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("chunks", "encoding", "body", "problem"),
    [
        ((b"123", b"456"), None, b"123456", None),
        ((b"123", b"4567"), None, None, "response_body_limit"),
        ((b"1234",), "gzip", None, "unsupported_content_encoding"),
    ],
)
def test_streaming_enforces_limit_without_content_length(
    client,
    monkeypatch,
    chunks,
    encoding,
    body,
    problem,
):
    stream = ChunkStream(chunks)
    headers = {"Content-Type": "Application/JSON; charset=utf-8"}
    if encoding:
        headers["Content-Encoding"] = encoding
    response = httpx.Response(200, headers=headers, stream=stream)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(lambda _: response))
    monkeypatch.setattr(transport, "MAX_BYTES", 6)
    captured = client.acquire(httpx.Request("GET", "http://127.0.0.1:8080/api"))
    assert captured == Acquisition(200, "application/json", body, problem)
    assert stream.closed and client.attempted == 1


def test_hard_deadline_requires_the_main_thread(target):
    with ThreadPoolExecutor() as executor:
        with pytest.raises(InputError, match="POSIX main process"):
            executor.submit(LoopbackTransport, target, 1).result(timeout=2)


def test_existing_process_timer_is_preserved():
    import signal

    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        with (
            pytest.raises(InputError, match="unused process timer"),
            transport._deadline(1),
        ):
            pytest.fail("Deadline must preserve the active timer")
        assert signal.getitimer(signal.ITIMER_REAL)[0] > 50
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
