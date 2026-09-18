# EXP-BUG-113 — SMTP secret-key failures fell back to stale credentials

**Mission / severity:** M18, P0.

The runtime SMTP resolver caught the deliberate `RuntimeError` raised for a
missing or invalid encryption key and silently selected environment defaults.
Runtime delivery could therefore use a different credential after the settings
test failed closed. Encryption-key failures now propagate and stop delivery.
