# EXP-BUG-042 — queued summary source could change before delivery

**Mission / severity:** M08, P0.

The durable operation retained the producing pipeline and evidence hash, but the
worker later reloaded the run-wide summary document. A delayed pipeline-A task
could therefore distribute pipeline-B text under pipeline A's accepted review.

The outbox now freezes the executive narrative, panel and AI provenance with the
pipeline and evidence identity. The worker uses those immutable bytes and keeps
the Mongo lookup only for legacy operations, which fail closed under enforcement
when no immutable source accompanies their review identity.

**Green evidence:** exact candidate `21f1e98f` deployed as
`build-20260917-075932`; delayed-task regression and the notification replay
mutation harness.
