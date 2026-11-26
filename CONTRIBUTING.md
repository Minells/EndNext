# Contributing to EndNext

EndNext welcomes bug fixes, features, tests, and documentation improvements.
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) and use the
[Security Policy](SECURITY.md) to report suspected vulnerabilities privately.

## Set up a development environment

You need Git and Python 3.12 or newer with `venv` and `pip`. Use Python 3.12
to match CI. From the repository root on macOS or Linux, run the following
commands in a POSIX shell.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]' build
```

The editable install exposes the `endnext` CLI. Try the
[quickstart](README.md#installazione-e-demo) or run
`endnext inspect examples/items.openapi.json`.

## Choose a contribution

- **Bugs.** Search [existing issues][issues], then use the bug report form.
  Include the EndNext version, the command and minimal synthetic inputs, and
  the expected and observed results.
- **Features.** Use the feature request form to describe the use case and
  desired behavior. Discuss substantial CLI, report format, or transport
  changes before implementing them.
- **Documentation.** Open a pull request directly for small corrections.
  Update the relevant [README](README.md), [examples](examples/), or
  [documentation](docs/) when behavior or commands change, and verify the
  examples you edit.

Use synthetic contracts and fixtures in tests and examples. Keep offline
commands independent of network access. HTTP integration tests run as part of
the pytest suite and use local loopback servers. The [validator notes](docs/VALIDATOR.md)
explain the current input and validation choices.

## Run the checks

With the virtual environment active, run these commands from the repository
root. Use `python -m ruff format src tests` to apply formatting when needed.

```sh
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy
python -m pytest --cov --cov-report=term-missing --cov-report=xml
python -m build
```

The coverage floor is 96% across statements and branches. `python -m build`
produces a wheel and source distribution under `dist/`. CI installs both in
clean environments and exercises the CLI on Linux and macOS.
The **Audit Python dependencies** job checks the resolved Python environment,
including transitive dependencies, against published PyPI advisories.
The [M6 verification report](docs/RELEASE_CANDIDATE.md) records the package
checks and examples from independent projects.

## Open a pull request

1. Start a short-lived branch from current `main` in your fork, or in the
   repository if you have write access. Target `main` in the pull request.
2. Keep commits focused and use a short, imperative subject, such as
   `Clarify baseline comparison output`.
3. Explain the change and its motivation, link the related issue when there
   is one, and list the checks you ran. Call out compatibility or security
   implications without disclosing vulnerability details.
4. Include regression coverage for behavior changes and update affected
   documentation or examples. A pull request is ready to merge after the
   maintainer has reviewed the change and **EndNext merge gate** passes.

GitHub deletes the source branch after the pull request merges. Update your
local `main` and remove your work branch once you have confirmed that its
changes are included.

[issues]: https://github.com/Minells/EndNext/issues
