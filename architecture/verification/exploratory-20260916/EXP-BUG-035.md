# EXP-BUG-035 — terminal release redaction leaked the original verdict

**Mission / severity:** M08, P0.

Rejected and superseded projections cleared the current recommendation and
narrative fields but left `original_recommendation`. Automated pass-rate band
downgrades populate that field even without a human override, exposing the
model verdict through release readiness and embedded reports.

Terminal projections now clear `original_recommendation` in release readiness,
release webhooks, and report-embedded verdicts. Targeted mutations remove each
redaction independently.

**Green evidence:** exact homelab candidate `843a6565`, focused M08 suite and
four-mutation terminal-review harness.
