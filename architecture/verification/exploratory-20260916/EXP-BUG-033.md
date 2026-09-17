# EXP-BUG-033 — notification audits described failed deliveries

**Mission / severity:** M08, P1.

The relay committed distribution audits immediately after refreshing every
claimed row, before checking live routing, digest authorization, provider
success, or the token-fenced outcome. Missing routes and failed providers could
therefore create `distributed_unreviewed` or `distribution_would_refuse` records.

The relay now carries the decision beside the refreshed bytes and stages its
audit only after a successful provider response and successful delivery-token
compare-and-set, in the outcome transaction.

**Green evidence:** exact homelab candidate `843a6565`, focused outbox tests and
six-mutation notification replay harness.
