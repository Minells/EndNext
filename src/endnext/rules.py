"""Independent assertions over one complete or partial observation."""

from urllib.request import parse_http_list

from .contract import Contract, ResponseSelection
from .evidence import Assertion, Operation, Verdict
from .inputs import Fixture, InputError, body_forbidden, is_json_media_type


def verify(
    contract: Contract,
    fixture: Fixture,
    *,
    imported: bool,
) -> tuple[Operation, tuple[Assertion, ...]]:
    selection = contract.select_response(fixture)
    documented = selection.media_types is not None
    status = Assertion(
        "response.status.documented",
        "PASS" if documented else "FAIL",
        "An HTTP status documented for the operation.",
        "The operation documents the observed HTTP status."
        if documented
        else "No response in the operation matches the observed HTTP status.",
    )
    media = _media_assertion(fixture, selection)
    schema = _schema_assertion(
        contract,
        fixture,
        media,
        selection=selection,
        imported=imported,
    )
    assertions = [schema, status, media, _expected_status(fixture)]
    if imported and fixture.expected_access is not None:
        assertions.append(_expected_access(fixture))
    if imported and fixture.expected_cache_directive is not None:
        assertions.append(_cache_directive(fixture))
    return selection.operation, tuple(assertions)


def _media_assertion(fixture: Fixture, selection: ResponseSelection) -> Assertion:
    rule = "response.media_type.documented"
    expectation = "The documented response media type."
    if not selection.media_types or body_forbidden(fixture.method, fixture.status):
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
    if selection.media is not None:
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
    selection: ResponseSelection,
    imported: bool,
) -> Assertion:
    rule = "response.body.schema"
    expectation = "The documented JSON response body schema."
    if body_forbidden(fixture.method, fixture.status):
        condition = "HEAD" if fixture.method == "head" else f"HTTP {fixture.status}"
        expectation = f"No body for {condition}."
        if fixture.body_state == "absent":
            return Assertion(
                rule, "SKIPPED", expectation, "The observation has no body."
            )
        if fixture.body_state == "truncated":
            return Assertion(
                rule,
                "INCONCLUSIVE",
                expectation,
                "The partial observation cannot establish whether there was a body.",
            )
        return Assertion(rule, "FAIL", expectation, "The observation contains a body.")
    if fixture.body_state in {"invalid", "truncated"}:
        return Assertion(
            rule,
            "INCONCLUSIVE",
            expectation,
            "The observed body is malformed or incomplete.",
        )
    if fixture.body_state == "absent":
        verdict: Verdict = "FAIL" if selection.media_types else "SKIPPED"
        return Assertion(rule, verdict, expectation, "The observation has no body.")
    if media.verdict != "PASS":
        return Assertion(
            rule,
            "SKIPPED",
            expectation,
            "A documented media type is required for schema validation.",
        )
    if not is_json_media_type(fixture.media_type):
        return Assertion(
            rule,
            "INCONCLUSIVE",
            expectation,
            "The observed body uses a format outside JSON validation.",
        )
    try:
        _, assertion = contract.check(fixture, selection)
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


def _expected_access(fixture: Fixture) -> Assertion:
    identity = "anonymous" if fixture.anonymous else "authenticated"
    rule = f"security.{identity}_access"
    outcome = "allowed" if fixture.expected_access else "denied"
    expectation = f"{identity.capitalize()} access {outcome}."
    if 200 <= fixture.status <= 299:
        observed = True
    elif fixture.status == 403 or (fixture.anonymous and fixture.status == 401):
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
        f"The imported {identity} observation returned HTTP {fixture.status}.",
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
