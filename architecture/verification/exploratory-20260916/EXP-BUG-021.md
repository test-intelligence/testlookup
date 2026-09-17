# EXP-BUG-021 — distinct Investigator subjects superseded one another

**Mission / requirement / severity:** M07, distinct narrative review subjects,
P1.

Generic newer-run supersession grouped reviews by test run and workflow type.
Two Investigator narratives for the same parent run therefore collided even
though each has its own deterministic pipeline subject and evidence hash.

Investigator subjects now rely on the one-live-row-per-exact-subject invariant
and skip same-run workflow supersession. Ordinary workflow reruns retain their
existing newer-run behavior.

**Red evidence:** `c3d66890` superseded the first of two pending Investigator
subjects.

**Green evidence:** exact deployed `bbce4f5f` kept both subjects pending in unit
and real PostgreSQL tests, then accepted Investigator A while Investigator B and
the parent report remained pending.

**Mutation:** restoring generic Investigator supersession was killed by both
subject-isolation regressions; the harness asserts exact applicability.
