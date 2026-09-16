# M01 authentication

**State:** RUNNING.
**Stable environment:** homelab tag `build-20260916-144609`, schema `0189`,
source revision `b785170ea906e2d73993a90da9d1ada307a7aa3a`.
**Contract oracle:** `architecture/testing/EXPLORATORY_MISSIONS.md` M01 and the
current authentication routes/tests.

The real happy-path login, protected deep-link resume, cross-tab logout,
interrupted-refresh recovery, and profile persistence checks passed in
Chromium, Firefox, and WebKit. A bounded API probe also confirmed invalid
credential rejection, eight concurrent authenticated reads, single-use refresh
rotation, logout revocation, and rejection of both tokens after logout. Mocked
cases remain component evidence only.

The synthetic first-time reset journey exposed EXP-BUG-005. The backend
correctly revoked the bootstrap token family, while the frontend navigated to
`/overview` with those revoked credentials before the first protected read
forced a login redirect. The strengthened live trace and component regression
are red on the deployed revision. M01 remains RUNNING until the fixing commit
is deployed exactly, the component and live regressions pass, mutation is
killed, and independent review approves the change.
