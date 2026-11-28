"""Build deterministic plans from an inventory and local case configuration."""

import math
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Literal

from .evidence import Inventory, Operation, SecurityRequirements
from .inputs import CASE_NAME, METHODS, PATH_PARAMETER, Fixture, InputError, read_file
from .policy import MAX_REQUESTS

MAX_CASES = 256
MAX_FIXTURE_FILES = 16

AUTHORIZATION_RULE = "security.authenticated_access"
type OperationKey = tuple[str, str]
type Scalar = str | int | float
type Source = Literal["fixture", "http"]
type Access = Literal["allow", "deny"]
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


@dataclass(frozen=True)
class Case:
    name: str
    operation: OperationKey
    source: Source
    expected_status: int | None
    fixture: Path | None
    path_parameters: dict[str, Scalar]
    query_parameters: dict[str, Scalar]
    auth_profile: str | None = None
    expected_access: Access | None = None


@dataclass(frozen=True)
class PlanConfig:
    selected: frozenset[OperationKey]
    cases: tuple[Case, ...]
    request_budget: int
    target: object = None
    auth_profiles: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "PlanConfig":
        try:
            data = tomllib.loads(
                read_file(path, "plan configuration").decode("utf-8-sig")
            )
        except (ValueError, RecursionError):
            raise InputError(
                "The plan configuration contains invalid UTF-8 TOML."
            ) from None
        budget = data.get("request_budget", 0)
        if type(budget) is not int or not 0 <= budget <= MAX_REQUESTS:
            raise InputError("The plan request budget must be an integer from 0 to 20.")
        select = data.get("select")
        if not isinstance(select, list):
            raise InputError("The plan requires a select array of operation names.")
        selected = frozenset(_parse_operation(value) for value in select)
        if len(selected) != len(select):
            raise InputError("The plan selects the same operation more than once.")
        cases = data.get("cases", [])
        if not isinstance(cases, list):
            raise InputError("The plan cases must be an array of tables.")
        if len(cases) > MAX_CASES:
            raise InputError("The plan exceeds the 256-case limit.")
        parsed = tuple(
            sorted(
                (_parse_case(case, path.parent) for case in cases),
                key=lambda case: case.name,
            )
        )
        if len({case.name for case in parsed}) != len(parsed):
            raise InputError("The plan uses a case name more than once.")
        profiles = _parse_profiles(data.get("auth_profiles", {}))
        if any(
            case.auth_profile is not None and case.auth_profile not in profiles
            for case in parsed
        ):
            raise InputError("An HTTP case references an undefined auth profile.")
        return cls(selected, parsed, budget, data.get("target"), profiles)


def _parse_profiles(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise InputError("Auth profiles must be a table of named profiles.")
    profiles = {}
    for name, profile in raw.items():
        if not CASE_NAME.fullmatch(name) or not isinstance(profile, dict):
            raise InputError("Each auth profile requires a valid name and table.")
        env = profile.get("bearer_env")
        if (
            set(profile) != {"bearer_env"}
            or not isinstance(env, str)
            or not _ENV_NAME.fullmatch(env)
        ):
            raise InputError(
                "Each auth profile requires only a bearer_env variable name."
            )
        profiles[name] = env
    return profiles


def _parse_operation(value: object) -> OperationKey:
    if isinstance(value, str):
        method, _, template = value.partition(" ")
        if method.lower() in METHODS and template.startswith("/"):
            return method.upper(), template
    raise InputError(
        "Each plan operation must have an HTTP method and an exact path template."
    )


def _parse_case(raw: object, directory: Path) -> Case:
    if not isinstance(raw, dict):
        raise InputError("Each plan case must be a table.")
    name = raw.get("name")
    source = raw.get("source")
    status = raw.get("expected_status")
    fixture = raw.get("fixture")
    profile = raw.get("auth_profile")
    access = raw.get("expected_access")
    if not isinstance(name, str) or not CASE_NAME.fullmatch(name):
        raise InputError("Each plan case requires a valid name.")
    if source not in ("fixture", "http"):
        raise InputError("Plan case source must be fixture or http.")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise InputError("Plan expected_status must be an integer from 100 to 599.")
    if profile is not None or access is not None:
        if (
            source != "http"
            or not isinstance(profile, str)
            or not CASE_NAME.fullmatch(profile)
            or access not in ("allow", "deny")
            or status is not None
        ):
            raise InputError(
                "An authorization case requires an HTTP source, auth_profile and "
                "expected_access (allow or deny) in place of expected_status."
            )
    if fixture is not None:
        if (
            source != "fixture"
            or not isinstance(fixture, str)
            or not fixture
            or "\x00" in fixture
        ):
            raise InputError("Only fixture cases may specify a local fixture filename.")
        if "://" in fixture or fixture.startswith(("//", "\\")):
            raise InputError("Plan fixtures must use local filesystem paths.")
    path_parameters = _parameters(raw.get("path_parameters", {}))
    query_parameters = _parameters(raw.get("query_parameters", {}))
    if source == "fixture" and (path_parameters or query_parameters):
        raise InputError("Imported parameters belong in the fixture.")
    return Case(
        name,
        _parse_operation(raw.get("operation")),
        source,
        status,
        directory / fixture if fixture is not None else None,
        path_parameters,
        query_parameters,
        profile,
        access,
    )


def _parameters(raw: object) -> dict[str, Scalar]:
    if not isinstance(raw, dict):
        raise InputError("Plan parameters must be a table of scalars.")
    result: dict[str, Scalar] = {}
    for name, value in raw.items():
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise InputError("Plan parameters must be strings or finite numbers.")
        if isinstance(value, float) and not math.isfinite(value):
            raise InputError("Plan parameters must be strings or finite numbers.")
        result[name] = value
    return result


@dataclass(frozen=True)
class CandidateRule:
    rule: str
    condition: str


_STATUS = CandidateRule(
    "response.status.documented", "An observed status is available."
)
_MEDIA = CandidateRule(
    "response.media_type.documented",
    "A documented response and its observed media type are available.",
)
_SCHEMA = CandidateRule(
    "response.body.schema",
    "A complete response and its documented body schema are available.",
)
_EXPECTED = CandidateRule(
    "response.status.expected", "The owner supplied an expected status."
)
_ACCESS = CandidateRule(
    AUTHORIZATION_RULE,
    "The test credential is available and the response establishes an access outcome.",
)


@dataclass(frozen=True)
class PlannedCase:
    name: str
    source: Source
    state: Literal["PLANNED", "SKIPPED"]
    reason: str | None
    requires_request: bool
    request_cost: int
    candidate_rules: tuple[CandidateRule, ...]
    auth_profile: str | None = None
    expected_access: Access | None = None


@dataclass(frozen=True)
class PlannedOperation:
    operation: Operation
    selected: bool
    effective_security: SecurityRequirements
    security_source: Literal["operation", "document", "none"]
    allows_anonymous: bool
    cases: tuple[PlannedCase, ...]


@dataclass(frozen=True)
class Plan:
    operations: tuple[PlannedOperation, ...]
    request_budget: int
    requests_planned: int
    cases_planned: int
    cases_skipped: int
    candidate_assertions: int


def build_plan(
    inventory: Inventory,
    config: PlanConfig,
    *,
    allows_http: Callable[[Case], bool] | None = None,
) -> Plan:
    known = {
        (item.operation.method, item.operation.path_template): item
        for item in inventory.operations
    }
    if config.selected.difference(known):
        raise InputError("A selected operation is absent from the contract.")
    by_operation: dict[OperationKey, list[Case]] = {}
    for case in config.cases:
        if case.operation not in known:
            raise InputError("A case operation is absent from the contract.")
        by_operation.setdefault(case.operation, []).append(case)

    @cache
    def fixture_operation(path: Path) -> OperationKey | str:
        if not path.exists():
            return "fixture_missing"
        try:
            fixture = Fixture.load(path)
        except InputError:
            return "fixture_invalid"
        return fixture.method.upper(), fixture.path_template

    fixture_paths = {
        case.fixture
        for case in config.cases
        if case.fixture is not None and case.operation in config.selected
    }
    if len(fixture_paths) > MAX_FIXTURE_FILES:
        raise InputError("The plan exceeds the 16-file fixture limit.")

    operations = []
    used = 0
    for key, item in known.items():
        inherited = inventory.declared_security
        security = item.declared_security
        source: Literal["operation", "document", "none"] = "operation"
        if security is None:
            security = inherited if inherited is not None else []
            source = "document" if inherited is not None else "none"
        anonymous = not security or {} in security
        selected = key in config.selected
        cases = []
        for case in by_operation.get(key, []):
            reason = _skip_reason(
                case,
                selected,
                anonymous,
                used < config.request_budget,
                allows_http,
                fixture_operation,
            )
            cost = int(case.source == "http" and reason is None)
            rules: tuple[CandidateRule, ...] = (_STATUS, _MEDIA, _SCHEMA)
            if case.auth_profile is not None:
                rules = (_ACCESS,)
            elif case.expected_status is not None:
                rules += (_EXPECTED,)
            cases.append(
                PlannedCase(
                    case.name,
                    case.source,
                    "PLANNED" if reason is None else "SKIPPED",
                    reason,
                    case.source == "http",
                    cost,
                    rules if reason is None else (),
                    case.auth_profile,
                    case.expected_access,
                )
            )
            used += cost
        operations.append(
            PlannedOperation(
                item.operation, selected, security, source, anonymous, tuple(cases)
            )
        )

    outcomes = [case for operation in operations for case in operation.cases]
    planned = sum(case.state == "PLANNED" for case in outcomes)
    return Plan(
        tuple(operations),
        config.request_budget,
        used,
        planned,
        len(outcomes) - planned,
        sum(len(case.candidate_rules) for case in outcomes),
    )


def _skip_reason(
    case: Case,
    selected: bool,
    anonymous: bool,
    has_budget: bool,
    allows_http: Callable[[Case], bool] | None,
    fixture_operation: Callable[[Path], OperationKey | str],
) -> str | None:
    if not selected:
        return "operation_not_selected"
    if case.source == "fixture":
        if case.fixture is None:
            return "fixture_missing"
        actual = fixture_operation(case.fixture)
        if isinstance(actual, str):
            return actual
        if actual != case.operation:
            return "fixture_operation_mismatch"
        return None

    method, template = case.operation
    if case.auth_profile is None:
        if (
            method != "GET"
            or case.expected_status is None
            or not 200 <= case.expected_status <= 299
        ):
            return "http_requires_positive_get"
        if not anonymous:
            return "authentication_required"
    elif method != "GET":
        return "authorization_requires_get"
    if (
        set(case.path_parameters) != set(PATH_PARAMETER.findall(template))
        or "" in case.path_parameters.values()
    ):
        return "path_parameters_missing_or_invalid"
    if allows_http is not None and not allows_http(case):
        return "outside_target_scope"
    if not has_budget:
        return "request_budget_exhausted"
    return None
