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
forced a login redirect. Exact revision `2f106277550c0a0f18a1400ab6f92f1d788cc7e6`
fixed that direct transition and passed its component and mutation checks.

The deployed follow-up journey then exposed EXP-BUG-006. Password reset and
the cleared server flag commit correctly, but whole-second JWT and cutoff
comparisons reject an immediately issued permanent-login token as if it
predated the cutoff. Exact revision `1195a4bb2b834624c219cac935f616cf8456fcf3`
fixed that comparison: 45 focused regressions, six mutations, and an immediate
deployed API reset/login/read probe passed.

The next cross-browser run exposed EXP-BUG-008. All three browsers retained
`/reset-password` as the login return target and resumed it after the permanent
login despite the server flag being false. Exact revision
`e40fbddb564d6139c9ac2ad0c899773d0da4608c` fixed the return path and passed the
complete reset/onboarding journey in Chromium, Firefox, and WebKit.

Independent review then found EXP-BUG-009: the durable cutoff used PostgreSQL
transaction-start time, and session issuance did not serialize with password
changes. A concurrent old-password login, refresh, or MFA exchange could
therefore escape revoke-all. The candidate now uses statement-time cutoffs,
copies the exact durable cutoff to Redis, locks the user row across every
session issuance/password-change boundary, and invalidates predating MFA
interstitial tokens. M01 remains RUNNING until this combined fix is deployed
exactly, its unit/integration/mutation/live suites pass, and independent review
approves it.
