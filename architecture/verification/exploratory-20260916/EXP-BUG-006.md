# EXP-BUG-006 — whole-second revocation cutoff rejected a new token

**Mission / requirement / severity:** M01, first-time password reset and token
revocation, P1.

The revocation table stores a precise cutoff, but access-token `iat` claims,
dependency parsing, Redis cutoffs, and durable comparisons reduced time to
whole seconds. The fail-closed `<=` comparison then rejected a permanent-login
token created after reset whenever both events occurred within one second.
Live API probes proved the password change and `must_change_password=false`
were committed; the freshly returned token nevertheless failed `/auth/me`.

Access tokens and both cutoff authorities now retain subsecond Unix time.
Existing whole-second tokens remain compatible, equality remains revoked, and
unparseable cutoff data still fails closed.

**Regression tests:** the core suite pins precise JWT issuance, dependency
propagation, cutoff writes, legacy Redis migration, Redis comparison, and
durable Postgres comparison. The deployed
`frontend/tests/e2e/first-time-reset-live.spec.ts` remains the end-to-end oracle
for immediate permanent-password sign-in and onboarding.

**Red evidence:** both precision regressions failed before the change. In the
deployed `2f106277` browser run, Chromium and WebKit returned to
`/reset-password` and Firefox remained on `/login`; immediate live API reads
rejected a newly issued permanent-login token.

**Green evidence, mutation, review, and deployed retest:** pending deployment
of the fixing commit.
