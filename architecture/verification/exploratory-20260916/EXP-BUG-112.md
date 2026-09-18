# EXP-BUG-112 — Runtime email delivery ignored the saved SMTP password

**Mission / severity:** M18, P0.

The SMTP test endpoint loaded the encrypted password from `secret_refs`, but
the runtime email resolver returned only non-secret `app_settings` metadata.
Notification and digest delivery could therefore attempt unauthenticated SMTP
after a successful settings test. Runtime delivery now resolves the encrypted
password through the same secret authority, with legacy and environment
fallbacks retained for existing deployments. Encryption-key failures propagate
instead of silently selecting a stale environment credential.
