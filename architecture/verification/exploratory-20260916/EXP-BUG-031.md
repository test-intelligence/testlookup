# EXP-BUG-031 — release readiness borrowed another pipeline's review

**Mission / severity:** M08, P0.

`release.decided` retried against its persisted decision pipeline, while the
release-readiness route used the newest deep review for the run. A later
pipeline could therefore authorize an older decision and the API could disagree
with its webhook.

The route now loads the persisted decision pipeline and passes it into the same
projection used by the webhook. Router-level regression and mutation tests pin
the exact subject.

**Green evidence:** exact homelab candidate `843a6565`, focused M08 suite and
four-mutation advisory/readiness harness.
