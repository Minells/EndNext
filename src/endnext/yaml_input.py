"""Read YAML contracts as bounded, JSON-compatible data."""

import math
import re
from collections.abc import Hashable
from pathlib import Path
from typing import cast

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from .inputs import (
    MAX_DEPTH,
    MAX_NODES,
    InputError,
    Json,
    JsonObject,
    decode_json,
    walk,
)

_TAG = "tag:yaml.org,2002:"
_TAGS = {_TAG + tag for tag in ("map", "seq", "str", "int", "float", "bool", "null")}


class _ContractLoader(yaml.SafeLoader):
    # Narrow implicit scalar typing to JSON-compatible values. OpenAPI field
    # names such as "on" and date-like examples remain strings.
    yaml_implicit_resolvers = {}

    def __init__(self, stream: str):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def compose_node(self, parent: Node | None, index: int) -> Node:
        self.nodes += 1
        if self.depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise InputError("The contract exceeds the input structure limit.")
        if self.check_event(AliasEvent):
            raise InputError(
                "YAML aliases are unsupported. Use local OpenAPI references."
            )
        self.depth += 1
        try:
            node = super().compose_node(parent, index)
        finally:
            self.depth -= 1
        assert node is not None
        if node.tag not in _TAGS:
            raise InputError("The contract contains an unsupported YAML tag.")
        return node

    def construct_mapping(
        self,
        node: MappingNode,
        deep: bool = False,
    ) -> dict[Hashable, Json]:
        result: dict[Hashable, Json] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise InputError(
                    "YAML mapping keys must be strings. Quote numeric keys."
                )
            if key in result:
                raise InputError("The contract contains a duplicate YAML key.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


_ContractLoader.add_implicit_resolver(
    _TAG + "bool", re.compile(r"^(?:true|false)$", re.I), "tTfF"
)
_ContractLoader.add_implicit_resolver(
    _TAG + "null", re.compile(r"^(?:null|~|)$", re.I), ["~", "n", "N", ""]
)
_ContractLoader.add_implicit_resolver(
    _TAG + "int", re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$"), "-+0123456789"
)
_ContractLoader.add_implicit_resolver(
    _TAG + "float",
    re.compile(r"^[+-]?(?:\d+\.\d+([eE][+-]?\d+)?|\d+[eE][+-]?\d+)$"),
    "-+0123456789",
)


def decode_contract(path: Path, data: bytes) -> JsonObject:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        return decode_json(data, "contract")
    try:
        obj = yaml.load(data.decode("utf-8-sig"), Loader=_ContractLoader)
    except (KeyError, ValueError, RecursionError, yaml.YAMLError):
        raise InputError("The contract contains invalid UTF-8 YAML.") from None
    if not isinstance(obj, dict):
        raise InputError("The contract must be a YAML mapping.")
    result = cast(JsonObject, obj)
    for node in walk(result, "contract"):
        if isinstance(node, float) and not math.isfinite(node):
            raise InputError("The contract contains a non-finite number.")
    return result
