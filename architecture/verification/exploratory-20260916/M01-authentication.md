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
login despite the server flag being false. M01 remains RUNNING until the
return-path fix is deployed exactly, the complete browser journey passes,
mutation is green, and independent review approves the combined M01 changes.
