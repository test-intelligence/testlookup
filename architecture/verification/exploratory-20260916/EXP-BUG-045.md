# EXP-BUG-045 — valid override event depended on an agent report

**Mission / severity:** M08, P1.

The immutable report requirement was applied to human overrides as well as agent
decisions. A valid override on a legacy decision, or after critic publication
failed, could therefore commit without producing its subscribed event.

Only agent-triggered events require the immutable report. Override events use the
committed human council value and remain independent of agent report availability.

**Green evidence:** exact candidate `21f1e98f` deployed as
`build-20260917-075932`; override-without-report regression and the webhook
replay mutation harness.
