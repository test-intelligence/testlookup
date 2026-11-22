---
name: testlookup-cli-quality
description: Review and improve TestLookup CLI tests. Use for command behavior, profiles, upload and receipt handling, output formats, exit codes, and CI verdict automation.
---

# TestLookup CLI Quality

## Identify the consumer contract

- Read `cli/README.md`, `cli/pyproject.toml` and `cli/testlookup_cli/app.py`. Locate the command group, shared transport, login or key resolution, project or release selector, and formatter. Compare the shipped help and argument contracts with actual usage.
- Verify machine-readable output, stderr diagnostics and exit codes for success, invalid input, missing configuration, timeout and server failure as relevant. Check scripts that consume the command before changing JSON keys, output layout or a verdict exit code.
- For upload and wait behavior, check request metadata, source identity on retry, wait expiry and terminal failure. Verify receipt completion and run readback independently. For `ci-verdict`, use fixtures for each supported result and assert the exit code alongside the reported reason.

## Verify client and installed behavior

- Run `python -m pytest cli/tests` with the CLI installed and the required test dependencies, or select focused cases such as `cli/tests/test_upload_retry_and_wait.py`, `cli/tests/test_command_exit_codes.py` and `cli/tests/test_ci_verdict_command.py`. Keep temporary profiles and synthetic credentials isolated from the user's configuration.
- Inspect `e2e-runner/live/test_cli_uploads.py` for installed CLI verification against an isolated candidate. If entry points or packaging change, install the built wheel into a clean environment and check `testlookup --help` plus a changed command.
- Record command and package version, input and profile scope, exact exit status, sanitized output and backend result. Link any remaining release obligation to its runner ID.
