import json
from pathlib import Path

import pytest
from test_check import PASS, SENTINEL

from endnext.inputs import (
    MAX_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    Fixture,
    InputError,
    decode_json,
    read_file,
    walk,
)
from endnext.yaml_input import decode_contract


@pytest.mark.parametrize(
    "raw",
    [
        b'{"key": 1, "key": 2}',
        b'{"number": 1e400}',
        b'{"number": NaN}',
        b'{"number": Infinity}',
        b'{"number": -Infinity}',
        b'{"data": "\xff"}',
    ],
)
def test_json_rejects_ambiguous_or_nonfinite_data(raw):
    with pytest.raises(InputError, match="invalid UTF-8 JSON"):
        decode_json(raw, "fixture")


def test_json_document_requires_object():
    with pytest.raises(InputError, match="must be a JSON object"):
        decode_json(b"null", "fixture")


@pytest.mark.parametrize("kind", ["depth", "nodes"])
def test_structure_limits_include_the_root(kind):
    if kind == "nodes":
        within_limit = [None] * (MAX_NODES - 1)
        over_limit = [*within_limit, None]
        assert sum(1 for _node in walk(within_limit, "fixture")) == MAX_NODES
    else:
        within_limit = None
        for _ in range(MAX_DEPTH):
            within_limit = [within_limit]
        over_limit = [within_limit]
        assert sum(1 for _node in walk(within_limit, "fixture")) == MAX_DEPTH + 1
    with pytest.raises(InputError, match="exceeds the input structure limit"):
        list(walk(over_limit, "fixture"))


def test_input_limit_accepts_exact_size(tmp_path):
    path = tmp_path / "at-limit.json"
    path.write_bytes(b"{}" + b" " * (MAX_BYTES - 2))
    assert len(read_file(path, "fixture")) == MAX_BYTES


def test_file_read_failure_omits_underlying_exception(tmp_path, monkeypatch):
    path = tmp_path / "fixture.json"
    path.touch()

    def denied(*args, **kwargs):
        raise PermissionError(SENTINEL)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(InputError, match="must be a readable local file") as error:
        read_file(path, "fixture")
    assert SENTINEL not in str(error.value)


@pytest.mark.parametrize(
    ("fields", "diagnostic"),
    [
        ({"case": ""}, "case identifier"),
        ({"method": "FETCH"}, "supported HTTP method"),
        ({"path_template": "items/{id}"}, "requires an OpenAPI path template"),
        ({"path_parameters": {}}, "path parameters must match"),
        ({"path_parameters": {"id": True}}, "non-empty strings or numbers"),
        ({"status": True}, "integer HTTP status"),
        ({"status": 99}, "integer HTTP status"),
        ({"status": 600}, "integer HTTP status"),
        ({"body_state": "unknown"}, "body_state must be"),
        (
            {"media_type": "application/json; charset=utf-8"},
            "media type without parameters",
        ),
        ({"media_type": None}, "requires a body field"),
        ({"body_state": "absent"}, "body field requires body_state complete"),
        ({"expected_status": None}, "expected_status must be an integer"),
        ({"expected_access": None}, "fixture expected_access"),
        ({"expected_cache_directive": None}, "directive name"),
        ({"expected_status": True}, "expected_status must be an integer"),
        ({"identity": "unknown"}, "fixture identity"),
        ({"expected_access": "yes"}, "fixture expected_access"),
        ({"sensitive": "yes"}, "fixture sensitive"),
        ({"expected_cache_directive": "no-store=max-age"}, "directive name"),
        ({"headers": {"Cache-Control": 10}}, "object of string values"),
    ],
)
def test_fixture_rejects_invalid_observation_metadata(tmp_path, fields, diagnostic):
    data = json.loads(PASS.read_text())
    data.update(fields)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError, match=diagnostic):
        Fixture.load(path)


@pytest.mark.parametrize(
    ("raw", "diagnostic"),
    [
        (b"- not-a-mapping", "must be a YAML mapping"),
        (b"200: value", "mapping keys must be strings"),
        (b"number: 1e400", "non-finite number"),
        (b"number: !!float .inf", "non-finite number"),
        (
            b"value: " + b"[" * (MAX_DEPTH + 1) + b"null" + b"]" * (MAX_DEPTH + 1),
            "exceeds the input structure limit",
        ),
    ],
)
def test_yaml_contract_rejects_unsupported_shapes(tmp_path, raw, diagnostic):
    with pytest.raises(InputError, match=diagnostic):
        decode_contract(tmp_path / "openapi.yaml", raw)
