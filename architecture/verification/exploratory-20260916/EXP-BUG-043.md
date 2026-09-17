# EXP-BUG-043 — release event could precede immutable report publication

**Mission / severity:** M08, P0.

`ReleaseRiskAgent` emitted after committing its mutable SQL decision but before
the immutable DecisionReport existed. On a same-pipeline retry this could pair a
rewritten SQL decision with an older report hash.

The early emitter is removed. `DecisionReportCriticAgent` is now the only agent
producer and emits only after it publishes the immutable report. Agent payload
decision fields come from that report and its evidence hash must match the
requested subject.

**Green evidence:** exact candidate `21f1e98f` deployed as
`build-20260917-075932`; pre-publication, immutable-bytes and hash-mismatch
regressions plus the webhook replay mutation harness.
