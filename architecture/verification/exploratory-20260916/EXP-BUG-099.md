# EXP-BUG-099 — Plan execution accepted arbitrary status strings

**Mission / severity:** M16, P1.

The execution request schema accepted any string even though counts and UI
semantics recognize a closed set. The API now accepts only `passed`, `failed`,
`blocked` and `skipped`.
