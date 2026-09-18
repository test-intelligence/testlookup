# EXP-BUG-110 — Clearing a global connector secret restored the old value

**Mission / severity:** M18, P1.

An empty integration secret was excluded from secret extraction, so the API
could report it cleared while the encrypted row survived and reappeared on
reload. Global connector writes now treat omission as keep, empty as expire,
and non-empty as replace for every supported secret field.
