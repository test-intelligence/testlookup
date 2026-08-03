# Severity rubric

Assign exactly one severity per finding. When in doubt, pick the higher one and say why.

| Severity | Meaning | Examples |
|---|---|---|
| **Blocker** | Ships a bug, data loss, security hole, or breaks a hard invariant. Must fix before merge. | Untrusted SQL string-format; secret committed; ingestion path skips `finalize_run`; migration with no `downgrade`; `AI_OFFLINE_MODE` bypassed; tenant scope missing on a list endpoint; response_model field never populated (client gets nulls). |
| **Major** | Likely-wrong, untested, or a contract mismatch that will bite under real data. Should fix before merge. | Changed source file with no test in the diff; frontend type ↔ backend schema field mismatch; multiple Alembic heads; `db.rollback()` in a service; structlog positional `%s`; missing `await db.refresh` after flush on a serialized onupdate column. |
| **Minor** | Real but low-impact; correctness holds. Fix soon, can merge with a note. | Inconsistent naming; missing edge-case test for a non-critical branch; suboptimal query that still works; thin-but-not-empty router doing one cheap transform. |
| **Nit** | Style/preference; no behavioral impact. Optional. | Comment typo; import ordering; a clearer variable name; redundant parenthesis. |

## Verdict mapping

- Any **Blocker** or **Major** open → `BLOCKED`.
- Only **Minor**/**Nit** → `READY WITH NITS`.

This skill never emits "APPROVED" — approval requires the executable gate (`/code-reviewer`: tests, lint, type-check green). State that explicitly in the action plan's next-step.

## De-duplication rule

If the same root cause surfaces in both Pass 1 and Pass 2, keep the **cross-file** framing (it shows the full blast radius) and drop the per-file duplicate, but cite all involved lines.
