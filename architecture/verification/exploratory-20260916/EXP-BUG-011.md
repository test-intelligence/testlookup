# EXP-BUG-011 — admin-created temporary password was not forced to reset

**Mission / requirement / severity:** M01, first-time login and session
authority, P1.

The admin user-management route describes and returns a temporary password,
and the UI tells the administrator to share it as such. The created `User` row
did not set `must_change_password`, however, so its model default remained
false. A successful login therefore issued an ordinary session and the
protected route never sent the new user to `/reset-password`. The disclosed
bootstrap credential remained the account's permanent password.

**Red evidence:** while preparing the deployed M02 MCP isolation repetition,
an exact `05ae055aa119ffb42268b7cf9a29467b5480d1d9` admin-created user returned
`must_change_password=false`. An in-cluster read confirmed that the stored
credential was still the generated bootstrap credential; the synthetic user
was deactivated during cleanup.

**Fix:** admin creation now sets `must_change_password=True`, reusing the
existing first-time-reset route and session-revocation behavior.

**Green evidence:** exact revision
`f051798cef9a9913cd12ab3f23a23a42fdb28f6c` passed all 19 user-management
tests. A live admin-created user returned `must_change_password=true`; its
bootstrap JWT was rejected after first-time reset; the permanent password then
logged in with `must_change_password=false`. The synthetic account was
deactivated during cleanup.

**Mutation:**
`scripts/mutation_check_exploratory_m01_admin_temp_password.py` removed the
forced-reset assignment, asserted the replacement applied exactly once, and
the focused regression failed. The harness restored the source byte-for-byte.

**Homelab authority:** tag `build-20260916-194417`; backend and worker digest
`sha256:ef19ea92aeaaa8e0ffaca85ee0a2b42eae9b784d673d50e53c1805648eee5042`;
frontend digest
`sha256:f79f0d3f0b1860cd2b48d2d45d7880cc364796b7d1058ffe871f90c5d964668e`;
MCP digest
`sha256:b01b5c380ab0039a077223ddfcd5c8469adbf0fa5a77aa8245024f13bbd89070`.
All nine application deployments were fully available on those exact images;
`/health/version` reported the exact revision, readiness/details were healthy,
and Alembic reported `0189 (head)`.

**Independent review:** APPROVE. The reviewer found the runtime assignment,
ORM-row regression, single-replacement mutation, deployed reset/revocation
journey, and closure evidence adequate. Reviewed staged diff hash:
`17b170100beb1edf159969c16e5388cec3ba9ffe`.
