"""Independent assertions over one complete or partial observation."""

from urllib.request import parse_http_list

from .contract import Contract
from .evidence import Assertion, Operation, Verdict
from .inputs import Fixture, InputError


def verify(
    contract: Contract,
    fixture: Fixture,
    *,
    imported: bool,
) -> tuple[Operation, tuple[Assertion, ...]]:
    operation, media_types = contract.response_profile(fixture)
    documented = media_types is not None
    status = Assertion(
        "response.status.documented",
        "PASS" if documented else "FAIL",
        "An HTTP status documented for the operation.",
        "The operation documents the observed HTTP status."
        if documented
        else "No response in the operation matches the observed HTTP status.",
    )
    media = _media_assertion(fixture, media_types)
    schema = _schema_assertion(
        contract,
        fixture,
        media,
        has_documented_body=bool(media_types),
        imported=imported,
    )
    assertions = [schema, status, media, _expected_status(fixture)]
    if imported and fixture.anonymous and fixture.expected_access is not None:
        assertions.append(_anonymous_access(fixture))
    if imported and fixture.expected_cache_directive is not None:
        assertions.append(_cache_directive(fixture))
    return operation, tuple(assertions)


def _media_assertion(fixture: Fixture, media_types: list[str] | None) -> Assertion:
    rule = "response.media_type.documented"
    expectation = "The documented response media type."
    if not media_types or fixture.status == 204:
        return Assertion(
            rule,
            "SKIPPED",
            expectation,
            "The selected response has no applicable body media type.",
        )
    if fixture.media_type is None:
        verdict: Verdict = (
            "SKIPPED" if fixture.body_state == "absent" else "INCONCLUSIVE"
        )
        return Assertion(
            rule, verdict, expectation, "The observation has no media type."
        )
    family = fixture.media_type.partition("/")[0] + "/*"
    if {fixture.media_type, family, "*/*"}.intersection(media_types):
        return Assertion(
            rule, "PASS", expectation, "The observed media type is documented."
        )
    return Assertion(
        rule,
        "FAIL",
        expectation,
        "The observed media type is outside the documented set.",
    )


def _schema_assertion(
    contract: Contract,
    fixture: Fixture,
    media: Assertion,
    *,
    has_documented_body: bool,
    imported: bool,
) -> Assertion:
    rule = "response.body.schema"
    expectation = "The documented JSON response body schema."
    if fixture.status == 204:
        if fixture.body_state == "absent":
            return Assertion(
                rule, "SKIPPED", "No body for HTTP 204.", "The observation has no body."
            )
        return Assertion(
            rule, "FAIL", "No body for HTTP 204.", "The observation contains a body."
        )
    if fixture.body_state in {"invalid", "truncated"}:
        return Assertion(
            rule,
            "INCONCLUSIVE",
            expectation,
            "The observed body is malformed or incomplete.",
        )
    if fixture.body_state == "absent":
        verdict: Verdict = "FAIL" if has_documented_body else "SKIPPED"
        return Assertion(rule, verdict, expectation, "The observation has no body.")
    if media.verdict != "PASS":
        return Assertion(
            rule,
            "SKIPPED",
            expectation,
            "A documented media type is required for schema validation.",
        )
    if fixture.media_type != "application/json":
        return Assertion(
            rule,
            "INCONCLUSIVE",
            expectation,
            "The observed body uses a format outside JSON validation.",
        )
    try:
        _, assertion = contract.check(fixture)
        return assertion
    except InputError:
        if imported:
            raise
        return Assertion(
            rule,
            "INCONCLUSIVE",
            expectation,
            "The response schema could not be applied.",
        )


def _expected_status(fixture: Fixture) -> Assertion:
    rule = "response.status.expected"
    if fixture.expected_status is None:
        return Assertion(
            rule,
            "SKIPPED",
            "An owner-supplied HTTP status.",
            "The owner supplied no expected HTTP status.",
        )
    expected = fixture.expected_status
    verdict: Verdict = "PASS" if fixture.status == expected else "FAIL"
    return Assertion(
        rule,
        verdict,
        f"HTTP {expected}.",
        f"The observation returned HTTP {fixture.status}.",
    )


def _anonymous_access(fixture: Fixture) -> Assertion:
    rule = "security.anonymous_access"
    expectation = (
        "Anonymous access allowed."
        if fixture.expected_access
        else "Anonymous access denied."
    )
    if 200 <= fixture.status <= 299:
        observed = True
    elif fixture.status in {401, 403}:
        observed = False
    else:
        return Assertion(
            rule,
            "INCONCLUSIVE",
            expectation,
            "This status does not establish the declared access outcome.",
        )
    verdict: Verdict = "PASS" if observed == fixture.expected_access else "FAIL"
    return Assertion(
        rule,
        verdict,
        expectation,
        f"The imported anonymous observation returned HTTP {fixture.status}.",
    )


def _cache_directive(fixture: Fixture) -> Assertion:
    rule = "security.cache_directive"
    expected = fixture.expected_cache_directive
    directives = {
        item.partition("=")[0].strip().lower()
        for item in parse_http_list(fixture.cache_control or "")
    }
    verdict: Verdict = "PASS" if expected in directives else "WARN"
    reason = (
        "The declared cache directive is present."
        if verdict == "PASS"
        else "The response marked sensitive lacks the declared cache directive."
    )
    return Assertion(rule, verdict, f"Cache-Control includes {expected}.", reason)
