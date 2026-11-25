"""Bounded loading and validation of local observations."""

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]

MAX_BYTES = 1_048_576
MAX_DEPTH = 64
MAX_NODES = 100_000
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
CASE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")
MEDIA_TYPE = re.compile(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+")
CACHE_DIRECTIVE = re.compile(r"[a-zA-Z0-9_-]{1,64}")
type BodyState = Literal["complete", "absent", "invalid", "truncated"]


class InputError(Exception):
    """An error whose message is safe to show to the CLI user."""


def read_file(path: Path, label: str) -> bytes:
    try:
        if not path.is_file():
            raise InputError(f"The {label} must be a readable local file.")
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
    except OSError:
        raise InputError(f"The {label} must be a readable local file.") from None
    if len(data) > MAX_BYTES:
        raise InputError(f"The {label} exceeds the 1 MiB input limit.")
    return data


def _unique_object(pairs: list[tuple[str, Json]]) -> JsonObject:
    result: JsonObject = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON key")
        result[name] = value
    return result


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    return value


def _invalid_constant(raw: str) -> None:
    raise ValueError("Non-finite JSON constant")


def walk(value: Json, label: str) -> Iterator[Json]:
    """Visit a bounded JSON tree without recursively walking user input."""
    stack = [(value, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > MAX_DEPTH or count > MAX_NODES:
            raise InputError(f"The {label} exceeds the input structure limit.")
        yield node
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)


def decode_json_value(data: bytes, label: str) -> Json:
    try:
        value = json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_invalid_constant,
        )
    except (ValueError, RecursionError):
        raise InputError(f"The {label} contains invalid UTF-8 JSON.") from None
    return cast(Json, value)


def decode_json(data: bytes, label: str) -> JsonObject:
    value = decode_json_value(data, label)
    if not isinstance(value, dict):
        raise InputError(f"The {label} must be a JSON object.")
    return value


@dataclass(frozen=True)
class Fixture:
    case: str
    method: str
    path_template: str
    path_parameters: dict[str, str]
    status: int
    body: Json
    filename: str
    media_type: str | None = "application/json"
    body_state: BodyState = "complete"
    expected_status: int | None = None
    anonymous: bool = False
    expected_access: bool | None = None
    expected_cache_directive: str | None = None
    cache_control: str | None = None

    @classmethod
    def load(cls, path: Path) -> "Fixture":
        obj = decode_json(read_file(path, "fixture"), "fixture")
        for _ in walk(obj, "fixture"):
            pass

        case = obj.get("case")
        method = obj.get("method")
        template = obj.get("path_template")
        parameters = obj.get("path_parameters", {})
        status = obj.get("status")
        media_type = obj.get("media_type")

        if not isinstance(case, str) or not CASE_NAME.fullmatch(case):
            raise InputError(
                "The fixture requires a case identifier using letters, digits, _, . or -."
            )
        if not isinstance(method, str) or method.lower() not in METHODS:
            raise InputError("The fixture requires a supported HTTP method.")
        if not isinstance(template, str) or not template.startswith("/"):
            raise InputError("The fixture requires an OpenAPI path template.")
        if not isinstance(parameters, dict) or set(parameters) != set(
            PATH_PARAMETER.findall(template)
        ):
            raise InputError(
                "The fixture path parameters must match its path template."
            )
        if any(
            isinstance(value, (bool, dict, list)) or value is None or value == ""
            for value in parameters.values()
        ):
            raise InputError(
                "Fixture path parameters must be non-empty strings or numbers."
            )
        if type(status) is not int or not 100 <= status <= 599:
            raise InputError(
                "The fixture requires an integer HTTP status from 100 to 599."
            )
        body_state = obj.get(
            "body_state",
            "absent" if status == 204 and "body" not in obj else "complete",
        )
        if body_state not in ("complete", "absent", "invalid", "truncated"):
            raise InputError(
                "Fixture body_state must be complete, absent, invalid or truncated."
            )
        if media_type is not None and (
            not isinstance(media_type, str) or not MEDIA_TYPE.fullmatch(media_type)
        ):
            raise InputError(
                "The fixture media type must be a media type without parameters."
            )
        if body_state == "complete" and ("body" not in obj or media_type is None):
            raise InputError(
                "The fixture requires a body field containing the observed JSON."
            )
        if body_state != "complete" and "body" in obj:
            raise InputError("The fixture body field requires body_state complete.")
        expectation = obj.get("expected_status")
        if expectation is not None and (
            type(expectation) is not int or not 100 <= expectation <= 599
        ):
            raise InputError(
                "Fixture expected_status must be an integer from 100 to 599."
            )
        identity = obj.get("identity")
        access = obj.get("expected_access")
        if identity not in (None, "anonymous", "authenticated") or (
            access is not None and type(access) is not bool
        ):
            raise InputError(
                "Use an explicit fixture identity and a boolean expected_access."
            )
        cache = obj.get("expected_cache_directive")
        sensitive = obj.get("sensitive", False)
        if type(sensitive) is not bool or (
            cache is not None
            and (not isinstance(cache, str) or not CACHE_DIRECTIVE.fullmatch(cache))
        ):
            raise InputError(
                "Use a boolean sensitive flag and an HTTP cache directive name."
            )
        headers = obj.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(value, str) for value in headers.values()
        ):
            raise InputError("Fixture headers must be an object of string values.")
        cache_control = next(
            (
                str(value)
                for key, value in headers.items()
                if key.lower() == "cache-control"
            ),
            None,
        )

        return cls(
            case,
            method.lower(),
            template,
            {key: str(value) for key, value in parameters.items()},
            status,
            obj.get("body"),
            path.name,
            media_type.lower() if isinstance(media_type, str) else None,
            cast(BodyState, body_state),
            expectation,
            identity == "anonymous",
            access,
            cache.lower() if sensitive and isinstance(cache, str) else None,
            cache_control,
        )
