# EXP-BUG-036 — advisory release inclusion was not audited

**Mission / severity:** M08, P1.

QA Lead and Admin callers could deliberately request `allow_advisory=true`, but
the unreviewed value left the route without the distribution audit required for
every inclusion.

The exact-subject projection now stages `ai_report.distributed_unreviewed` with
the actor, project, run, review, and advisory channel. The route commits that
audit before returning the advisory value.

**Green evidence:** exact homelab candidate `843a6565`, router and service
regressions plus the four-mutation advisory harness.
