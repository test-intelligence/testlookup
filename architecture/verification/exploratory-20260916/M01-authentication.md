# M01 authentication

**State:** RUNNING.
**Stable environment:** homelab tag `build-20260916-164412`, schema `0189`,
source revision `5297fea9b6dd9d538a58190b0c5168f91209e592`.
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
keeps caller-transaction cutoffs in PostgreSQL until commit, locks the user row
across every session issuance/password-change boundary, and invalidates
predating MFA interstitial tokens.

Exact revision `5297fea9b6dd9d538a58190b0c5168f91209e592` is now
deployed and healthy. The focused backend suite passed 142 tests; the real
PostgreSQL concurrency regression passed against homelab; 22 cutoff/session,
four reset-transition, and two return-path mutations were killed. The complete
reset → permanent login → empty-project onboarding journey passed in Chromium,
Firefox, and WebKit. A second deployed journey enrolled TOTP, changed the
password, immediately completed a fresh MFA challenge, and read `/auth/me`.
Ruff and TypeScript are clean; the earlier unchanged-surface gates remain green.
M01 remains RUNNING only for the independent re-review of the remediated race.
