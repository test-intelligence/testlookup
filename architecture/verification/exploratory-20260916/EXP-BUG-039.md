# EXP-BUG-039 — release webhook retries were not evidence-bound

**Mission / severity:** M08, P0.

A queued `release.decided` payload retained its pipeline ID but not the evidence
hash. Changed evidence under that pipeline could therefore authorize stale
queued bytes.

Release events now start only after the immutable DecisionReport exists, include
its evidence hash, and recheck pipeline plus evidence before every provider
attempt. Legacy rows without the hash fail closed under enforcement.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; focused release webhook tests and the webhook replay
mutation harness.
