# EXP-BUG-120 — Python SDK download omitted required modules

**Mission / severity:** M22, P1.

`/api/v1/sdk/python` returned only `testlookup_reporter.py`, while that module
imports `ci_context` and `commit_range`. A clean downloaded copy therefore
failed before it could create a client. The endpoint now returns an explicit
six-file ZIP manifest. Regression coverage opens the archive and requires each
runtime sibling and installation file; the live archive imported successfully
from an isolated extraction directory.
