# EXP-BUG-108 — Connector probes ignored saved integration settings

**Mission / severity:** M18, P0.

The settings API persisted global connector metadata and encrypted credentials,
but Jira, Splunk, OpenShift, and GitHub probes read process environment values.
All configurable probes now receive one database-authoritative resolved config,
with regressions asserting the actual endpoint and authorization values used.
