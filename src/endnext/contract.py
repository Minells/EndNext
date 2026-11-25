"""OpenAPI 3.1 adapter for offline response checks."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote, urlsplit

from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema_path import SchemaPath
from openapi_core import Config, OpenAPI
from openapi_core.datatypes import RequestParameters
from openapi_core.templating.paths.datatypes import PathOperationServer
from openapi_core.templating.paths.finders import APICallPathFinder
from openapi_core.templating.paths.iterators import (
    SimplePathsIterator,
    SimpleServersIterator,
)
from openapi_core.templating.responses.exceptions import ResponseNotFound
from openapi_core.templating.responses.finders import ResponseFinder
from openapi_core.validation.response.exceptions import InvalidData
from openapi_core.validation.response.validators import V31ResponseDataValidator
from openapi_core.validation.schemas.exceptions import InvalidSchemaValue
from referencing.exceptions import Unresolvable

from .evidence import (
    Assertion,
    Inventory,
    InventoryOperation,
    Operation,
    SecurityRequirements,
)
from .inputs import (
    METHODS,
    PATH_PARAMETER,
    Fixture,
    InputError,
    JsonObject,
    read_file,
    walk,
)
from .yaml_input import decode_contract

_DIALECTS = {
    "https://spec.openapis.org/oas/3.1/dialect/base",
    "https://json-schema.org/draft/2020-12/schema",
}
_JSON_TYPES = {
    type(None): "null",
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}
_SCHEMA_RULE = "response.body.schema"


def _check_offline_references(document: JsonObject) -> None:
    # Inspect the entire tree before either validator can initiate resolution.
    references = (
        "$ref",
        "$dynamicRef",
        "$recursiveRef",
        "operationRef",
        "externalValue",
    )
    for node in walk(document, "contract"):
        if not isinstance(node, dict):
            continue
        for keyword in references:
            value = node.get(keyword)
            if isinstance(value, str) and not value.startswith("#"):
                raise InputError(
                    "External references are unsupported. Use same-document fragments."
                )
        if isinstance(node.get("$id"), str):
            raise InputError("Schema $id is unsupported in a single-document contract.")
        dialects = (node.get("$schema"), node.get("jsonSchemaDialect"))
        if any(
            isinstance(value, str) and value.rstrip("#") not in _DIALECTS
            for value in dialects
        ):
            raise InputError("The contract uses an unsupported schema dialect.")


class _ExactOperationFinder(APICallPathFinder):
    """Match the fixture's template literally and ignore server URLs."""

    paths_iterator = SimplePathsIterator("paths")
    servers_iterator = SimpleServersIterator()

    def find(self, method: str, name: str) -> PathOperationServer:
        return super().find(method, unquote(urlsplit(name).path.removeprefix("/")))


@dataclass
class _Request:
    method: str
    path: str
    path_pattern: str
    parameters: RequestParameters
    host_url: str = "https://offline.invalid"
    content_type: str = ""
    body: bytes | None = None

    @classmethod
    def from_fixture(cls, fixture: Fixture) -> "_Request":
        path = PATH_PARAMETER.sub(
            lambda match: quote(fixture.path_parameters[match[1]], safe=""),
            fixture.path_template,
        )
        # openapi-core joins the pattern to host_url before calling the finder.
        pattern = "/" + quote(fixture.path_template, safe="")
        return cls(
            fixture.method,
            path,
            pattern,
            RequestParameters(path=fixture.path_parameters),
        )


@dataclass
class _Response:
    status_code: int
    data: bytes
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Contract:
    api: OpenAPI
    digest: str

    @classmethod
    def load(cls, path: Path) -> "Contract":
        data = read_file(path, "contract")
        document = decode_contract(path, data)
        version = document.get("openapi")
        if not isinstance(version, str):
            raise InputError("The contract requires an OpenAPI version.")
        if not re.fullmatch(r"3\.1\.[0-9]+", version):
            raise InputError("Only OpenAPI 3.1.x contracts are supported.")
        _check_offline_references(document)
        try:
            api = OpenAPI.from_dict(
                document,
                config=Config(
                    response_validator_cls=V31ResponseDataValidator,
                    path_finder_cls=_ExactOperationFinder,
                    extra_media_type_deserializers={
                        "application/*": json.loads,
                        "*/*": json.loads,
                    },
                ),
            )
        except (SchemaError, ValidationError, Unresolvable, RecursionError):
            raise InputError(
                "The OpenAPI contract is invalid or has an unresolved reference."
            ) from None
        return cls(api, hashlib.sha256(data).hexdigest())

    def inventory(self) -> Inventory:
        paths = self.api.spec / "paths"
        operations = []
        try:
            if paths.exists():
                for template, path in sorted(paths.str_items()):
                    if not template.startswith("/"):
                        continue
                    for method in METHODS:
                        if method in path:
                            operations.append(
                                _inventory_operation(path / method, method, template)
                            )
            return Inventory(_declared_security(self.api.spec), operations)
        except (SchemaError, Unresolvable, RecursionError):
            raise InputError(
                "The OpenAPI contract has an invalid schema or reference."
            ) from None

    def check(self, fixture: Fixture) -> tuple[Operation, Assertion]:
        try:
            return self._check(fixture)
        except (SchemaError, Unresolvable, RecursionError):
            raise InputError(
                "The OpenAPI contract has an invalid schema or reference."
            ) from None

    def _check(self, fixture: Fixture) -> tuple[Operation, Assertion]:
        spec = self.api.spec / "paths" / fixture.path_template / fixture.method
        if not spec.exists():
            raise InputError("The fixture operation is absent from the contract.")
        operation = _operation(spec, fixture.method, fixture.path_template)
        selection = _select_response_schema(spec, fixture.status)
        if isinstance(selection, Assertion):
            return operation, selection

        request = _Request.from_fixture(fixture)
        response = _Response(
            fixture.status,
            json.dumps(fixture.body).encode("ascii"),
            selection,
        )
        error = next(self.api.iter_response_errors(request, response), None)
        if error is None:
            return operation, Assertion(
                _SCHEMA_RULE,
                "PASS",
                "The documented JSON response body schema.",
                "The response body matches its schema.",
            )
        if isinstance(error, InvalidData) and isinstance(
            error.__cause__, InvalidSchemaValue
        ):
            schema_error = next(iter(error.__cause__.schema_errors), None)
            if isinstance(schema_error, ValidationError):
                return operation, _schema_failure(schema_error)
        raise RuntimeError("Unexpected response validator error")


def _operation(spec: SchemaPath, method: str, template: str) -> Operation:
    return Operation(method.upper(), template, (spec / "operationId").read_str(None))


def _declared_security(spec: SchemaPath) -> SecurityRequirements | None:
    security = spec / "security"
    if not security.exists():
        return None
    return cast(SecurityRequirements, security.read_value())


def _media_types(spec: SchemaPath) -> list[str]:
    content = spec / "content"
    return sorted(content.str_keys()) if content.exists() else []


def _inventory_operation(
    spec: SchemaPath, method: str, template: str
) -> InventoryOperation:
    responses = spec / "responses"
    return InventoryOperation(
        operation=_operation(spec, method, template),
        request_media_types=_media_types(spec / "requestBody"),
        response_media_types={
            status: _media_types(response)
            for status, response in sorted(responses.str_items())
            if not status.startswith("x-")
        }
        if responses.exists()
        else {},
        declared_security=_declared_security(spec),
    )


def _select_response_schema(operation: SchemaPath, status: int) -> Assertion | str:
    responses = operation / "responses"
    if not responses.exists():
        raise InputError("The selected operation has no documented responses.")
    try:
        response = ResponseFinder(responses).find(str(status))
    except ResponseNotFound:
        return Assertion(
            "response.status.documented",
            "FAIL",
            "An HTTP status documented for the operation.",
            "No response in the operation matches the observed HTTP status.",
        )
    content = response / "content"
    if not content.exists():
        raise InputError("The selected contract response has no body schema.")
    # The fixture provides JSON, including when the contract declares a range.
    media_type = next(
        (
            candidate
            for candidate in ("application/json", "application/*", "*/*")
            if candidate in content
        ),
        None,
    )
    if media_type is None:
        return Assertion(
            "response.media_type.documented",
            "FAIL",
            "A documented application/json response.",
            "The selected contract response has no matching application/json media type.",
        )
    schema = content / media_type / "schema"
    if not schema.exists():
        raise InputError("The selected contract response has no body schema.")
    # Openapi-core 0.23.1 treats boolean root schemas as mapping objects.
    value = schema.read_value()
    if value is True:
        return Assertion(
            _SCHEMA_RULE,
            "PASS",
            "The documented JSON response body schema.",
            "The boolean response schema accepts any JSON body.",
        )
    if value is False:
        return Assertion(
            _SCHEMA_RULE,
            "FAIL",
            "The documented JSON response body schema.",
            "The boolean response schema rejects every JSON body.",
            "",
        )
    return media_type


def _schema_failure(error: ValidationError) -> Assertion:
    # Only static messages, schema paths and JSON type names go into the report.
    schema_path = list(error.absolute_schema_path)
    declared_names = {
        name
        for keyword, name in zip(schema_path, schema_path[1:], strict=False)
        if keyword == "properties" and isinstance(name, str)
    }
    segments = [
        str(part).replace("~", "~0").replace("/", "~1")
        if type(part) is int or part in declared_names
        else "[redacted]"
        for part in error.absolute_path
    ]
    pointer = "".join(f"/{part}" for part in segments)
    if error.validator == "type":
        observed = _JSON_TYPES[type(error.instance)]
        allowed = error.validator_value
        types = allowed if isinstance(allowed, list) else [allowed]
        if all(
            isinstance(item, str) and item in _JSON_TYPES.values() for item in types
        ):
            expected = " or ".join(str(item) for item in types)
            return Assertion(
                _SCHEMA_RULE,
                "FAIL",
                expected,
                f"The schema expects {expected}. The observed JSON type is {observed}.",
                pointer,
                observed,
            )
    return Assertion(
        _SCHEMA_RULE,
        "FAIL",
        "The documented JSON response body schema.",
        "A schema constraint failed at this location.",
        pointer,
    )
