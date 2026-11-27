"""Scope and request admission for explicitly authorized loopback traffic."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

from .inputs import PATH_PARAMETER, InputError

if TYPE_CHECKING:
    from .planner import Case

MAX_REQUESTS = 20


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
    def load(cls, raw: object) -> Target:
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

    def permits_request(self, request: httpx.Request) -> bool:
        if (
            request.method != "GET"
            or not self.permits(request.url)
            or request.headers.get_list("host") != [request.url.netloc.decode("ascii")]
            or request.headers.get_list("content-length") not in ([], ["0"])
            or {"transfer-encoding", "cookie", "authorization"}.intersection(
                request.headers
            )
        ):
            return False
        try:
            return request.content == b""
        except httpx.RequestNotRead:
            # Reject streams without consuming them.
            return False

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
        return request if self.permits_request(request) else None
