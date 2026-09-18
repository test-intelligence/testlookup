# EXP-BUG-096 — Stale lifecycle actions could transition changed cases

**Mission / severity:** M16, P0.

Direct lifecycle endpoints accepted an action without binding it to the case
version rendered by the client. Every action now carries `expected_version`,
locks the case and returns a refreshable 409 before any stale transition,
history row or audit row is staged.
