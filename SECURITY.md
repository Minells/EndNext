# Security policy

## Supported versions

EndNext is in pre-release development. The `main` branch is the only line
maintained for security fixes at this stage. Older snapshots and release
candidate branches do not receive backports.

## Use live checks within scope

Run `endnext scan` only against a loopback service you are authorized to test.
Specify the origin, port, and path prefix, and select the GET cases approved for
that service. Supply bearer credentials via environment variables referenced by
the test profiles. Keep real credentials and traffic out of examples and shared
reports.

Run `endnext resilience-proxy` with synthetic dependencies on loopback. It
accepts a fixed HTTP/1.1 GET route and reuses the scan transport's upstream
scope and bounds.

## Report a vulnerability privately

[Open a private vulnerability report][report] through GitHub. If the direct
link prompts you to sign in, sign in and select **Report a vulnerability** from
the repository's **Security** tab.

Include the affected EndNext version or commit, Python version and platform,
the behavior you observed, and who or what could be affected. Provide the
smallest steps and synthetic inputs that reproduce the issue. Redact
credentials, tokens, cookies, personal data, and real API traffic from reports
and attachments.

Keep vulnerability details and proposed fixes private while the report is
being handled. Use the private report thread for updates and avoid posting
details in issues, pull requests, or public comments. Report vulnerabilities
in a service assessed with EndNext to that service's owner.

## What happens next

The maintainer triages the report, confirms the impact and affected versions,
and may ask for more information in the private thread. For confirmed issues,
the maintainer coordinates a fix, its verification, and the timing of public
disclosure with the reporter. A GitHub security advisory can record the impact,
fix, and any mitigation when disclosure is appropriate.

[report]: https://github.com/Minells/EndNext/security/advisories/new
