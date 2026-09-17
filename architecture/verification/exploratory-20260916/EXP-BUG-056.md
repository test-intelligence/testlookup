# EXP-BUG-056 — flaky results shared identities across projects

**Mission / severity:** M09, P1.

Test fingerprints are not project-salted, but global flaky-search results used
the bare fingerprint as their entity identity. Same-test rows in two authorized
projects therefore created duplicate consumer keys. The identity now combines
project and fingerprint, with regression and mutation coverage.

**Green evidence:** final M09 candidate deployed before the focused regression
and mutation run.
