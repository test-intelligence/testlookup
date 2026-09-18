# EXP-BUG-046 — delayed agent event could contradict a human override

**Mission / severity:** M08, P1.

An override could commit after the SQL agent decision but before the report
critic emitted its event. The delayed agent event then published the older model
verdict after the authoritative human event.

Release publication now locks the decision row through durable event staging.
An override that commits first suppresses the delayed agent event; an agent event
that wins the lock is staged before the later override event.

**Green evidence:** exact candidate `b3a0d5ee` deployed as
`build-20260917-084451`; override-before-critic regression and the webhook replay
mutation harness.
