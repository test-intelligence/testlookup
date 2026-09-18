# EXP-BUG-093 — Workflow publication was not bound to immutable evaluation authority

**Mission / severity:** M13, P0.

Publication could trust stale evaluation fields or a mismatched draft digest,
and evaluating a published version could mutate its evidence. Publication now
locks the selected version, validates its digest, reruns the authoritative
100-run window and requires the fresh manifest checksum for a regression
acceptance. Published evaluations are immutable and exact repeated publication
is read-only.
