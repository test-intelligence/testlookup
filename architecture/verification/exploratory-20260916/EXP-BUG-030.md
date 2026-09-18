# EXP-BUG-030 — withheld notification invented a release signal

**Mission / severity:** M08, P0.

The durable AI-summary context used the normal release prefix when a review
withheld the model output. With no executive panel, that helper invented
`Release signal: CONDITIONAL GO`, so pending, rejected, and superseded messages
could still carry an actionable verdict.

The withheld prefix now contains only deterministic failure count, build, and
pass-rate facts. `test_withheld_notification_prefix_has_no_invented_release_signal`
pins the sink context and the notification replay mutation suite restores the
bad helper call to prove the regression fails.

**Green evidence:** exact homelab candidate `843a6565`, focused M08 suite and
six-mutation notification replay harness.
