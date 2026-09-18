# EXP-BUG-040 — legacy queued notification prefixes could invent a verdict

**Mission / severity:** M08, P0.

Rows staged by the earlier candidate stored an accepted release-signal prefix in
the withheld slot. Trusting that durable value after an upgrade could emit
`CONDITIONAL GO` beside a review-pending notice.

The relay now rebuilds the withheld prefix from deterministic run facts on every
attempt and ignores the stored withheld prefix. This repairs already queued rows
without a data migration.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; the legacy-row regression and the notification replay
mutation harness.
