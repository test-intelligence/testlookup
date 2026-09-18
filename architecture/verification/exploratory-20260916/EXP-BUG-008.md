# EXP-BUG-008 — login resumed an obsolete forced-reset route

**Mission / requirement / severity:** M01, first-time password reset and return
navigation, P1.

Clearing the revoked bootstrap session can cause `ProtectedRoute` to send the
browser to login with `/reset-password` as its return target. After the user
signs in with the permanent password and the server reports
`must_change_password=false`, `LoginPage` still honored that obsolete target
and navigated back to the forced-reset screen.

The reset transition now explicitly clears router state, and login treats
`/reset-password` as a completed authentication interstitial rather than a
resumable destination. Other protected deep links still resume normally.

**Regression tests:** `frontend/src/pages/LoginPage.test.tsx` supplies the
stale router state and requires `/overview`; the reset-page component test
requires cleared navigation state. The deployed
`frontend/tests/e2e/first-time-reset-live.spec.ts` is the end-to-end oracle.

**Red evidence:** on exact deployed revision `1195a4bb`, the API precision
probe passed but Chromium, Firefox, and WebKit all navigated from permanent
login back to `/reset-password`. The component regression reproduced the same
`navigate('/reset-password')` call.

**Green evidence, mutation, review, and deployed retest:** pending deployment
of the fixing commit.
