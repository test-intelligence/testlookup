# EXP-BUG-121 — CLI erased stable automation exit codes

**Mission / severity:** M22, P1.

The shared client correctly classified authentication, permission, not-found,
validation, and timeout failures, but command wrappers caught the resulting
`CLIError` and always exited 1. Commands now preserve mapped codes while
generic failures remain 1, and HTTP 400 is classified with 422 as validation.
The live probe observed foreign/not-found 5, invalid 2, revoked 3, and network
failure 1 without printing the credential.
