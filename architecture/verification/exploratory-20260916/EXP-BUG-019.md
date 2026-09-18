# EXP-BUG-019 — Investigator review could authorize a parent-run report

**Mission / requirement / severity:** M07, exact review subject authority, P1.

Investigator narratives and ordinary reports share a `test_run_id`. The generic
parent-run envelope selected the newest live report review without excluding
`workflow_type=investigation`, so an accepted Investigator review could project
accepted authority onto a still-pending parent summary or export.

Generic parent-run lookups now exclude Investigator workflow rows while
preserving legacy rows with a null workflow. Investigator excerpts continue to
use their exact pipeline-scoped lookup.

**Red evidence:** `c3d66890` failed the cross-subject SQL contract.

**Green evidence:** exact deployed `bbce4f5f` passed the focused envelope,
distribution, notification, export, and PostgreSQL suites.

**Mutation:** removing the parent Investigator exclusion and changing the exact
pipeline predicate were both killed; the harness asserts that each replacement
applies exactly once.
