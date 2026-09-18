# EXP-BUG-097 — Stale edits could overwrite a newer test-case version

**Mission / severity:** M16, P0.

Managed-case PATCH requests had no optimistic concurrency token, so a stale tab
could overwrite a newer edit. Updates now require `expected_version` and refuse
a mismatch before applying fields or creating version and audit rows.
