# Review Action Plan

- **Branch:** `<branch>` vs `main`
- **Reviewed:** <N files> · <M commits>
- **Date:** <YYYY-MM-DD>
- **Reports:** [`01-per-file.md`](./01-per-file.md) · [`02-integration.md`](./02-integration.md)

## Verdict

**`BLOCKED`** _(any Blocker/Major open)_ — or — **`READY WITH NITS`** _(only Minor/Nit)_

<One sentence: what the change does and the single biggest reason for the verdict.>

> Not an approval. Approval requires the executable gate — run `/code-reviewer` (tests + lint + type-check green) after Blockers are cleared.

## Action checklist (work top-down)

### Blockers — must fix before merge
- [ ] [Blocker] `file:line` — <what to do>. (src: per-file / integration)

### Major — fix before merge
- [ ] [Major] `file:line` — <what to do>.

### Minor — fix soon
- [ ] [Minor] `file:line` — <what to do>.

### Nits — optional
- [ ] [Nit] `file:line` — <what to do>.

## Tally

| Severity | Count |
|---|---|
| Blocker | 0 |
| Major | 0 |
| Minor | 0 |
| Nit | 0 |

## Coverage

- **Files reviewed (full read):** <list or count>
- **Files not fully read:** <list with reason, or "none">
- **Checks skipped:** <e.g. "no migrations touched — section 3 N/A", or "none">
- **Pass 1 method:** inline / fanned out to <N> subagents.

## Suggested next step

1. Address Blockers (and Majors) — hand the checklist above to `/developer`.
2. Re-run this skill on the updated diff to confirm findings cleared.
3. Run `/code-reviewer` for the executable test/lint/type gate before merge.
