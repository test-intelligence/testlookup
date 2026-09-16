# EXP-BUG-009 — concurrent session issuance could escape password revocation

**Mission / requirement / severity:** M01, password-change session revocation,
P1.

The durable cutoff used PostgreSQL `now()`, which is fixed at transaction
start. A password-change transaction could therefore store a cutoff older than
the revocation statement. More broadly, login and refresh read the user without
a row lock: a concurrent request could verify the old password or refresh token
before the reset committed and mint its replacement after the cutoff.

Password login, refresh, development login, MFA session exchange, SSO session
issuance, and every password-change path now serialize on the user's PostgreSQL
row. Whichever transaction locks first completes first: a session issued first
is covered by the later cutoff, while a password change committed first forces
the waiting login to re-read the new password. MFA interstitial tokens are also
checked against the cutoff after the row is locked.

The durable cutoff uses `clock_timestamp()` in both insert and conflict-update
paths and returns the stored value. Caller-owned transactions do not publish a
cutoff to Redis before commit, because a rollback would leave a phantom legacy
marker that could be reimported. PostgreSQL is checked first on every cutoff
read; the Redis copy remains only for the self-owned legacy path.

Independent review found one more same-second boundary: python-jose converted
MFA `iat` datetimes to integer seconds, and application-node time could drift
from the PostgreSQL cutoff clock. Access and MFA session JWTs now take an
explicit PostgreSQL `clock_timestamp()` while the user lock is held, and MFA
`iat` is encoded as a fractional NumericDate. A post-cutoff token therefore
uses the same clock and precision as the cutoff authority.

**Regression tests:**

- `test_auth_session_serialization.py` pins user-row locking for password login,
  refresh, and MFA exchange plus cutoff validation for MFA interstitials.
- `test_password_change_login_serialization_postgres.py` holds a real user-row
  lock in a password reset, starts an old-password login concurrently, and
  requires the login to wait and then fail after the new hash commits.
- `test_revoke_all_cutoff_uses_wall_clock_not_transaction_start` pins both
  statement-time cutoff branches.
- `test_auth_session_tokens.py` requires both access and MFA JWT wrappers to
  use PostgreSQL statement time, while the precision suite pins the fractional
  MFA wire claim.

**Red evidence:** the focused regression run failed all four new backend
contracts on exact deployed revision `e40fbddb564d6139c9ac2ad0c899773d0da4608c`.
The PostgreSQL concurrency test is executed in the post-deployment green run.

**Green evidence:** exact revision
`7b38d58af5f8a86f25155fe2c858dc2d1564aaee` was deployed before validation.
The focused backend suite passed 152 tests and both real PostgreSQL concurrency
and rollback-atomicity tests passed against homelab. The reset/onboarding
browser journey passed in Chromium, Firefox, and WebKit. A dedicated live API
journey enrolled TOTP,
changed the password, immediately exchanged the new MFA challenge, and used the
resulting access token on `/auth/me`.

**Mutation:** the combined cutoff/session harness killed 23 mutations,
including transaction-start time, missing locks on every direct issuer and
password-change path, bypassed MFA cutoff validation, lost self-owned cache
propagation, premature caller-owned cache publication, and application-clock
token issuance. The harness asserted exact application and restored source
bytes after every mutant.

**Independent re-review:** APPROVE, tracked review diff
`f299399c201e8bfefb1d61a67f8f441a6b0caac4`. The review additionally found and
closed a default-QA-lead self-block plus rollback/cache-resurrection boundary.
