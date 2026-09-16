# EXP-BUG-005 — first-time reset entered a revoked session

**Mission / requirement / severity:** M01, first-time password reset and token
revocation, P1.

The backend correctly revokes the bootstrap access token and refresh family
after a successful first-time password reset. The frontend nevertheless kept
those tokens and navigated to `/overview`. The first protected request then
failed and sent the user back to login, after briefly presenting a successful
welcome transition into a session that could not be used.

The reset page now clears the revoked local session, tells the user to sign in
with the permanent password, and navigates directly to `/login`. Backend token
revocation remains unchanged.

**Regression tests:**
`frontend/src/pages/ResetPasswordPage.test.tsx` pins local token clearing,
notification copy, and the login destination. The live
`frontend/tests/e2e/first-time-reset-live.spec.ts` creates an isolated user and
empty project, proves `/overview` is never visited after reset, signs in with
the permanent password, completes onboarding, verifies persistence after
reload, and removes the synthetic fixtures.

**Red evidence:** against deployed revision `b785170ea906e2d73993a90da9d1ada307a7aa3a`,
the live navigation trace contained `/overview` before `/login`; the component
regression observed no logout call.

**Green evidence, mutation, review, and deployed retest:** pending deployment
of the fixing commit.
