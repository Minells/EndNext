"""Load bounded resilience scenarios and materialize templates for replay."""

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .inputs import CASE_NAME, InputError, read_file

_MAX_ATTEMPTS = 20
_MAX_DELAY_MS = 5_000
_MAX_ELAPSED_MS = 60_000
_MAX_SEED = 2**63 - 1
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_ALGORITHM = "sha256-v1"

type Outcome = int | Literal["timeout", "disconnect"]
type StepOutcome = Outcome | Literal["forward"]


@dataclass(frozen=True)
class Step:
    outcome: StepOutcome
    delay_ms: int


@dataclass(frozen=True)
class Generation:
    seed: int
    template_digest: str
    algorithm: str = _ALGORITHM


@dataclass(frozen=True)
class Scenario:
    name: str
    expected_status: int
    min_attempts: int
    max_attempts: int
    recovery_budget_ms: int
    steps: tuple[Step, ...]
    digest: str
    generation: Generation | None = None

    @classmethod
    def load(cls, path: Path, *, allow_forward: bool = False) -> "Scenario":
        raw = read_file(path, "resilience scenario")
        return cls._parse(raw, allow_forward=allow_forward)

    @classmethod
    def _parse(
        cls, raw: bytes, *, allow_forward: bool, seed: int | None = None
    ) -> "Scenario":
        try:
            data = tomllib.loads(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            raise InputError(
                "The resilience scenario contains invalid UTF-8 TOML."
            ) from None
        if data.keys() - {
            "name",
            "expected_status",
            "min_attempts",
            "max_attempts",
            "recovery_budget_ms",
            "steps",
            "generation",
        }:
            raise InputError("The resilience scenario contains an unsupported option.")

        digest = hashlib.sha256(raw).hexdigest()
        generation = None
        if "generation" in data:
            if seed is not None:
                raise InputError("A template cannot contain generation metadata.")
            generation = _parse_generation(data["generation"])

        name = data.get("name")
        if not isinstance(name, str) or not CASE_NAME.fullmatch(name):
            raise InputError("The resilience scenario requires a valid name.")
        status = _integer(data.get("expected_status"), "expected_status", 200, 299)
        minimum = _integer(data.get("min_attempts"), "min_attempts", 1, _MAX_ATTEMPTS)
        maximum = _integer(
            data.get("max_attempts"), "max_attempts", minimum, _MAX_ATTEMPTS
        )
        budget = _integer(
            data.get("recovery_budget_ms"), "recovery_budget_ms", 0, _MAX_ELAPSED_MS
        )
        steps = data.get("steps")
        if not isinstance(steps, list) or not minimum <= len(steps) <= _MAX_ATTEMPTS:
            raise InputError(
                "The scenario needs at least min_attempts steps and at most 20."
            )
        parsed = tuple(
            _choose_step(step, position, seed=seed, allow_forward=allow_forward)
            for position, step in enumerate(steps, start=1)
        )
        if sum(step.delay_ms for step in parsed) > _MAX_ELAPSED_MS:
            raise InputError("The scenario exceeds 60000 ms of simulated latency.")
        return cls(name, status, minimum, maximum, budget, parsed, digest, generation)


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(
            f"Scenario {field} must be an integer from {minimum} to {maximum}."
        )
    return value


def _parse_generation(raw: object) -> Generation:
    if not isinstance(raw, dict) or raw.keys() != {
        "algorithm",
        "seed",
        "template_digest",
    }:
        raise InputError(
            "Generation metadata requires algorithm, seed, and template_digest."
        )
    if raw["algorithm"] != _ALGORITHM:
        raise InputError("The scenario generation algorithm is unsupported.")
    digest = raw["template_digest"]
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise InputError("The scenario requires a valid template digest.")
    return Generation(_integer(raw["seed"], "seed", 0, _MAX_SEED), digest)


def _choose_step(
    raw: object, position: int, *, seed: int | None, allow_forward: bool
) -> Step:
    if not isinstance(raw, dict) or "alternatives" not in raw:
        return _parse_step(raw, allow_forward=allow_forward)
    if seed is None:
        raise InputError(
            "Run resilience-generate before replaying a scenario with alternatives."
        )
    alternatives = raw["alternatives"]
    if (
        raw.keys() != {"alternatives"}
        or not isinstance(alternatives, list)
        or not 2 <= len(alternatives) <= _MAX_ATTEMPTS
    ):
        raise InputError(
            "A template step requires 2 to 20 alternatives in place of a fixed outcome."
        )
    options = tuple(
        _parse_step(step, allow_forward=allow_forward) for step in alternatives
    )
    key = f"endnext/resilience/{_ALGORITHM}:{seed}:{position}".encode("ascii")
    selection = int.from_bytes(hashlib.sha256(key).digest(), "big") % len(options)
    return options[selection]


def _parse_step(raw: object, *, allow_forward: bool) -> Step:
    if not isinstance(raw, dict) or raw.keys() - {"outcome", "delay_ms"}:
        raise InputError(
            "Each scenario step allows an outcome and an optional delay_ms."
        )
    outcome = raw.get("outcome")
    if outcome == "forward":
        if not allow_forward:
            raise InputError("A forward outcome requires the live resilience proxy.")
    elif outcome not in ("timeout", "disconnect"):
        outcome = _integer(outcome, "step outcome", 200, 599)
    delay = _integer(raw.get("delay_ms", 0), "step delay_ms", 0, _MAX_DELAY_MS)
    return Step(outcome, delay)


def materialize_scenario(path: Path, seed: int) -> str:
    """Return a concrete TOML scenario for a template and explicit seed."""
    seed = _integer(seed, "seed", 0, _MAX_SEED)
    raw = read_file(path, "resilience template")
    scenario = Scenario._parse(raw, allow_forward=True, seed=seed)
    lines = [
        f"name = {json.dumps(scenario.name)}",
        f"expected_status = {scenario.expected_status}",
        f"min_attempts = {scenario.min_attempts}",
        f"max_attempts = {scenario.max_attempts}",
        f"recovery_budget_ms = {scenario.recovery_budget_ms}",
        "",
        "[generation]",
        f'algorithm = "{_ALGORITHM}"',
        f"seed = {seed}",
        f'template_digest = "sha256:{scenario.digest}"',
    ]
    for step in scenario.steps:
        lines.extend(
            (
                "",
                "[[steps]]",
                f"outcome = {json.dumps(step.outcome)}",
                f"delay_ms = {step.delay_ms}",
            )
        )
    return "\n".join(lines) + "\n"
