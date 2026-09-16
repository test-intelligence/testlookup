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
paths, returns the stored value, and copies that exact timestamp to Redis. This
removes transaction-start skew and prevents the durable and cache authorities
from receiving different cutoffs.

**Regression tests:**

- `test_auth_session_serialization.py` pins user-row locking for password login,
  refresh, and MFA exchange plus cutoff validation for MFA interstitials.
- `test_password_change_login_serialization_postgres.py` holds a real user-row
  lock in a password reset, starts an old-password login concurrently, and
  requires the login to wait and then fail after the new hash commits.
- `test_revoke_all_cutoff_uses_wall_clock_not_transaction_start` pins both
  statement-time cutoff branches.

**Red evidence:** the focused regression run failed all four new backend
contracts on exact deployed revision `e40fbddb564d6139c9ac2ad0c899773d0da4608c`.
The PostgreSQL concurrency test is executed in the post-deployment green run.

**Green evidence:** exact revision
`230abe7d4caaaf827861f37c7ac6fc02b35f5604` was deployed before validation.
The focused backend suite passed 133 tests and the real PostgreSQL concurrency
test passed against homelab. The deployed browser journey passed in Chromium,
Firefox, and WebKit.

**Mutation:** the combined cutoff/session harness killed 11 mutations,
including transaction-start time, missing login/refresh/MFA row locks, and a
bypassed MFA cutoff check. The harness asserted exact application and restored
source bytes after every mutant.

**Independent re-review:** pending.
