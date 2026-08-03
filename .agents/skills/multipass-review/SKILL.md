---
name: multipass-review
description: Use this skill to run a structured, multi-pass code review of a change set and emit actionable reports. Pass 1 analyzes each changed file in isolation; Pass 2 runs cross-file integration checks (contracts, type alignment, migrations vs ORM, import/dependency coherence, end-to-end flow); Pass 3 consolidates findings into severity-ranked reports you can act on. Triggers on requests like "multi-pass review", "do a per-file then cross-file review", "review this branch and give me an action plan", "review the diff and write up findings", "thorough review of these changes". Defaults to the branch diff vs `main`; accepts an explicit file/path/PR scope in $ARGUMENTS. Do NOT use for a single quick glance at one file, for running CI, or for committing/merging — this skill reviews and reports only.
---

# Multi-Pass Review

You are running a **disciplined, multi-pass code review** that separates concerns by design:

1. **Pass 1 — Per-file analysis.** Each changed file judged *in isolation*: correctness, readability, file-local invariants, security, dead code. No cross-file reasoning yet — that is deliberately deferred so per-file issues are not lost in the noise of integration concerns.
2. **Pass 2 — Cross-file integration checks.** How the files fit *together*: API/contract alignment across layers, frontend↔backend type parity, migration↔ORM coherence, import/dependency direction, shared-invariant enforcement, end-to-end flow.
3. **Pass 3 — Reports.** Consolidate the two passes into severity-ranked, actionable reports written to disk so the developer (or a follow-up `/developer` run) can act on them line by line.

This skill **reviews and reports only**. Do NOT edit source, commit, push, or open a PR. The verdict and the reports are the deliverable.

## When to invoke

**Invoke when** the user asks for a multi-pass / per-file-then-cross-file review, a "thorough" review with an action plan, or a written-up findings report for a change set.

**Do NOT invoke when:** the user wants a one-line sanity check, wants CI run/fixed (`/fix-ci`), wants the change *implemented* (`/developer`), or wants the final merge gate with full test/lint/type execution (`/code-reviewer` — this skill is complementary; it is analysis + reports, not a green-checks gate). If the user wants both, run this skill first for the written analysis, then point them at `/code-reviewer` for the executable gate.

## Step 0 — Establish scope and the report directory

1. Resolve scope:
   - If `$ARGUMENTS` names files/paths/globs, that is the scope.
   - If `$ARGUMENTS` is a PR number, resolve its files with `gh pr view <n> --json files` and `gh pr diff <n>`.
   - Otherwise default to the branch diff vs `main`: `git status`, `git diff --stat main...HEAD`, `git diff main...HEAD`, `git log --oneline main..HEAD`.
2. Build the **file manifest** — the concrete list of added/modified files in scope. This list drives Pass 1.
3. Read `git diff main...HEAD` once to understand intent, then **read each changed file in full** — the diff window hides surrounding context that breaks invariants.
4. Choose the report output directory. Derive a slug from the branch name and create:
   `docs/reviews/<branch-slug>/` (this repo gitignores `docs/`, so review artifacts stay local — confirmed; do not expect them in `git status`). If `docs/` is not writable for some reason, fall back to `.Codex/review-reports/<branch-slug>/`. Tell the user the exact path you chose.

Use the `Read`/`Grep`/`Glob` tools, not shell `cat`/`grep`.

## Step 1 — Pass 1: Per-file analysis

For **every** file in the manifest, evaluate it against `references/per-file-checklist.md`. Load that file now with `Read`.

For each file produce a block of findings. Each finding MUST have: `file:line`, a one-line title, severity (see `references/severity-rubric.md`), the problem, and the concrete fix. A clean file gets an explicit "no findings" line — never silently omit a file, or the report reads as "everything covered" when it wasn't.

**Scaling Pass 1.** Per-file analysis is embarrassingly parallel. Choose based on manifest size:
- **≤ ~6 files:** do it inline yourself, file by file.
- **> ~6 files:** fan out — spawn one `Explore`/`general-purpose` subagent per file (or small batch) via the `Agent` tool, each returning structured findings for its file(s) against the per-file checklist, then you collate. This keeps each file's context focused and the review fast.
- **Very large change sets, or when the user said "workflow":** only then consider the `Workflow` tool (per-file pass as a pipeline stage). Do not reach for Workflow without that opt-in.

Write the collated result to `<report-dir>/01-per-file.md` using `templates/per-file-report.md` as the shape.

## Step 2 — Pass 2: Cross-file integration checks

Now reason across files. Load `references/cross-file-checklist.md` with `Read` and work through each integration dimension. These are the checks that per-file review *cannot* catch by construction:

- Contract alignment: router ↔ service ↔ ORM model ↔ Pydantic schema for each touched endpoint.
- Frontend ↔ backend parity: `frontend/src/types/*` and service-layer shapes vs the Pydantic response models they consume.
- Migration ↔ ORM coherence: new `migrations/versions/*` vs `models/postgres.py`; single Alembic head; up/down symmetry.
- Dependency direction & cycles: routers stay thin, services own logic, no service→router imports, no new import cycles.
- Shared-invariant enforcement across the flow (these are this repo's highest-frequency silent regressions — full list in the checklist): one ingestion pipeline (`finalize_run`), one analysis router (`classify_test`), one AI-config resolver, PII redaction at every boundary, feature-flag + `AI_OFFLINE_MODE` gating order, `ALL_PROJECTS_ID` never reaching the backend as a UUID, tenant scoping on list endpoints, effective-suite (`tc.suite_name` AND `tr.primary_suite_name`) query pattern.
- End-to-end trace: pick each user-facing flow the change touches and walk it across all the changed files together — does the data actually thread through?

Each cross-file finding cites **all** the files/lines involved, not just one. Write to `<report-dir>/02-integration.md` using `templates/integration-report.md`.

## Step 3 — Pass 3: Consolidated action plan + verdict

Merge both passes into `<report-dir>/00-action-plan.md` using `templates/action-plan.md`. This is the report the user acts on:

- Findings de-duplicated and grouped by severity (Blocker → Major → Minor → Nit) per `references/severity-rubric.md`.
- Each item is a checklist line: `- [ ] [SEV] file:line — what to do`.
- A short **Verdict**: `BLOCKED` (any Blocker/Major open) or `READY WITH NITS` (only Minor/Nit) — this skill never says "approved"; it isn't the executable gate.
- A **Coverage** note: which files were reviewed, which were too large to fully read, any check deliberately skipped (no silent caps).
- A **Suggested next step**, typically: "address Blockers via `/developer`, then run `/code-reviewer` for the executable test/lint/type gate."

## Final response to the user

Keep your chat reply short. State: the report directory path, the file count reviewed, the verdict, and the top 3 most severe findings inline. Point them at `00-action-plan.md` for the rest. Do not paste the full reports into chat — they are on disk.

## Reference & template files

Load on demand with `Read` from `.Codex/skills/multipass-review/`:

| File | Load when |
|---|---|
| `references/per-file-checklist.md` | Running Pass 1 |
| `references/cross-file-checklist.md` | Running Pass 2 |
| `references/severity-rubric.md` | Assigning severity / building the action plan |
| `templates/per-file-report.md` | Writing `01-per-file.md` |
| `templates/integration-report.md` | Writing `02-integration.md` |
| `templates/action-plan.md` | Writing `00-action-plan.md` |
