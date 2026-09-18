# EXP-BUG-056 — flaky results shared identities across projects

**Mission / severity:** M09, P1.

Test fingerprints are not project-salted, but global flaky-search results used
the bare fingerprint as their entity identity. Same-test rows in two authorized
projects therefore created duplicate consumer keys. The identity now combines
project and fingerprint, with regression and mutation coverage.

**Green evidence:** candidate `0c867696` (`build-20260917-104804`) was deployed
before 190 broad search tests, 10 focused regressions and all 16 asserted M09
mutations passed.
