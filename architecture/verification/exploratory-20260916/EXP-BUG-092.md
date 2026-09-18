# EXP-BUG-092 — Reviewer retries could escape shared model authority

**Mission / severity:** M13, P0.

Producer escalation, reviewer retry and the reviewer model checks used
inconsistent counters, and the supervisor's tier override was not guaranteed to
reach the retried producer. They now share bounded authority: producer retry
uses the supervisor override and shared step budget, while reviewer families
3/4 use the run-level atomic model budget without spending the loop counter.
