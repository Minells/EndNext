"""OpenAPI 3.1 adapter for offline response checks."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema_path import SchemaPath
from openapi_core import OpenAPI
from openapi_core.templating.responses.exceptions import ResponseNotFound
from openapi_core.templating.responses.finders import ResponseFinder
from openapi_core.validation.schemas import oas31_schema_validators_factory
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


@dataclass(frozen=True)
class ResponseSelection:
    operation: Operation
    media_types: list[str] | None
    media: SchemaPath | None = None


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
            api = OpenAPI.from_dict(document)
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

    def select_response(self, fixture: Fixture) -> ResponseSelection:
        spec = self.api.spec / "paths" / fixture.path_template / fixture.method
        if not spec.exists():
            raise InputError("The fixture operation is absent from the contract.")
        operation = _operation(spec, fixture.method, fixture.path_template)
        responses = spec / "responses"
        if not responses.exists():
            return ResponseSelection(operation, None)
        try:
            response = ResponseFinder(responses).find(str(fixture.status))
            media_types = _media_types(response)
            media = None
            if fixture.media_type is not None:
                family = fixture.media_type.partition("/")[0] + "/*"
                for candidate in (fixture.media_type, family, "*/*"):
                    if candidate in media_types:
                        media = response / "content" / candidate
                        break
            return ResponseSelection(operation, media_types, media)
        except ResponseNotFound:
            return ResponseSelection(operation, None)
        except (SchemaError, Unresolvable, RecursionError):
            raise InputError(
                "The OpenAPI contract has an invalid schema or reference."
            ) from None

    def check(
        self, fixture: Fixture, selection: ResponseSelection
    ) -> tuple[Operation, Assertion]:
        try:
            return selection.operation, self._check_body(fixture, selection)
        except (SchemaError, Unresolvable, RecursionError):
            raise InputError(
                "The OpenAPI contract has an invalid schema or reference."
            ) from None

    def _check_body(self, fixture: Fixture, selection: ResponseSelection) -> Assertion:
        if selection.media is None or "schema" not in selection.media:
            raise InputError("The selected contract response has no body schema.")
        schema = selection.media / "schema"
        value = schema.read_value()
        if value is False:
            return Assertion(
                _SCHEMA_RULE,
                "FAIL",
                "The documented JSON response body schema.",
                "The boolean response schema rejects every JSON body.",
                "",
            )
        if value is True:
            return Assertion(
                _SCHEMA_RULE,
                "PASS",
                "The documented JSON response body schema.",
                "The boolean response schema accepts any JSON body.",
            )
        validator = oas31_schema_validators_factory.create(self.api.spec, schema)
        error = next(validator.validator.iter_errors(fixture.body), None)
        if error is not None:
            return _schema_failure(error)
        return Assertion(
            _SCHEMA_RULE,
            "PASS",
            "The documented JSON response body schema.",
            "The response body matches its schema.",
        )


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
