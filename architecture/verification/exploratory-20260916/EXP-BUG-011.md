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

**Green evidence, mutation, independent review, and homelab retest:** pending.
