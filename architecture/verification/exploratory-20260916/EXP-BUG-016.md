# EXP-BUG-016 — agent recompute overwrote a human release override

**Mission / requirement / severity:** M06, override authority and immutable
pre-override facts, P1.

`ReleaseRiskAgent._persist_decision()` updated a persisted recommendation
without checking `human_override`. A later agent pass could therefore replace
an explicit QA verdict while leaving the reason attached, producing a row that
looked human-authorized but carried the agent's new recommendation.

The agent now locks the same decision row used by `apply_override`. It refreshes
current automated risk and evidence, changes the recommendation only when no
human override exists, and never rewrites `original_recommendation` or
`original_risk_score` after the first override.

**Red evidence:** against exact deployed revision `b7387dee`, a persisted QA
`GO` became agent `CONDITIONAL_GO` while retaining the QA reason.

**Green evidence:** commit `3dc242cb9b0727f82c4b8c471ab76511cbf1bd14`
passed the unit contract and two real PostgreSQL proofs. The controlled
override-first overlap holds the human row lock, proves recompute waits, then
asserts human `GO`, current automated risk 45, immutable original `NO_GO`/80,
and the complete audit entry. The final exact candidate `5d8dd8a5` passed all
five PostgreSQL journeys and 811 broad release tests.

**Mutation:** removing the row lock and replacing the override guard with an
unconditional recommendation assignment were both killed. The harness asserts
single application, bounded subprocesses, and byte-for-byte restoration.

**Independent review:** APPROVE on `3dc242cb`; reviewed tree
`4e866eb80a393a21bb0482cc5a2adcab95aa60c1` and diff
`0f09b9a7f9c6d395a7b7f827798307904ad8cb00`.
