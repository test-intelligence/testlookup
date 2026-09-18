# EXP-BUG-123 — upload HTTP failures lost stable CLI exit codes

## Failure

The upload POST and upload-status polling paths raised plain `Exception`
objects for HTTP failures. Their command wrapper therefore returned generic
exit code 1 even when the shared CLI contract assigns authentication,
permission, not-found, validation, or timeout codes.

## Fix and regression

Both paths now raise `map_http_error(...)`. Parameterized regressions exercise
the low-level POST and poll paths and the real `upload file` Typer wrapper.
The M22 mutation harness replaces each mapper call with the old plain
exception and requires the corresponding selector to fail with status 1.

## Verification

Review, exact deployed revision, and final command results are recorded in
`M22-cli-mcp-sdk-parity.md` and `defects.csv`.
