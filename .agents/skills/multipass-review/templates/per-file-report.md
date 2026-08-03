# Pass 1 — Per-File Review

- **Branch:** `<branch>` vs `main`
- **Scope:** <N files>
- **Reviewer:** multipass-review (Pass 1, per-file isolation)

> Each file judged in isolation. Cross-file concerns are in `02-integration.md`.

## Findings by file

### path/to/file_a.py
- [Blocker] path/to/file_a.py:42 — <title>. <one-sentence problem>. **Fix:** <concrete action>.
- [Minor] path/to/file_a.py:88 — <title>. <problem>. **Fix:** <action>.

### path/to/file_b.tsx
- [Major] path/to/file_b.tsx:15 — Untested change. Component logic changed with no matching `*.test.tsx` in the diff. **Fix:** add a render/interaction test.

### path/to/file_c.py — no findings.

## Per-file tally

| Severity | Count |
|---|---|
| Blocker | 0 |
| Major | 0 |
| Minor | 0 |
| Nit | 0 |

## Files not fully read
<List any file too large to read in full, with the line range covered, or "none">
