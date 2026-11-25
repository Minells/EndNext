"""Bounded loading and validation of local observations."""

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type JsonObject = dict[str, Json]

MAX_BYTES = 1_048_576
MAX_DEPTH = 64
MAX_NODES = 100_000
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
CASE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}")


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


def decode_json(data: bytes, label: str) -> JsonObject:
    try:
        value = json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_float=_finite_float,
            parse_constant=_invalid_constant,
        )
    except (ValueError, RecursionError):
        raise InputError(f"The {label} contains invalid UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise InputError(f"The {label} must be a JSON object.")
    return cast(JsonObject, value)


@dataclass(frozen=True)
class Fixture:
    case: str
    method: str
    path_template: str
    path_parameters: dict[str, str]
    status: int
    body: Json
    filename: str

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
        if not isinstance(media_type, str) or media_type.lower() != "application/json":
            raise InputError("The fixture media type must be application/json.")
        if "body" not in obj:
            raise InputError(
                "The fixture requires a body field containing the observed JSON."
            )

        return cls(
            case,
            method.lower(),
            template,
            {key: str(value) for key, value in parameters.items()},
            status,
            obj["body"],
            path.name,
        )
