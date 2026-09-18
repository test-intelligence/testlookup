# EXP-BUG-038 — queued summaries borrowed another pipeline's acceptance

**Mission / severity:** M08, P0.

Durable AI-summary rows retained only the test run. A later accepted deep pipeline
for the same run could therefore authorize an older queued narrative.

The outbox, worker, preference and digest rows now carry the producing pipeline
ID and evidence hash. Every provider attempt resolves that exact immutable
review subject; enforced legacy rows without both identifiers fail closed.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; focused notification/outbox tests and the notification
replay mutation harness.
