# The Fixer — validated flaky-test fixes (AI-2)

The Fixer is a scheduled, budgeted agent that picks your flakiest quarantined
tests, generates a **test-code-only** candidate fix, **validates it by rerunning
the test in a sandbox**, and — only in *suggest* mode — opens a **draft PR**.
It is the air-gapped answer to hosted "auto-fix flaky test" features: nothing is
surfaced unless it was actually validated.

## The one rule

**Zero unvalidated fixes are ever surfaced.** With no runner configured the
Fixer can only run shadow diagnostics; an `error` (infra trouble) is not the
same as a `failed` validation, and **neither opens a PR**. A PR is opened only
from a `validated` result in suggest mode.

## Modes (agent policy `fixer`)

| mode | what it does |
|------|--------------|
| **shadow** (default) | select → diagnose → generate → validate, ledger only, **no PR** |
| **suggest** | shadow + open a **draft** PR for validated fixes |
| **act** | reserved — rejected at the policy layer (the Fixer never merges) |

## Config

`GET/PUT /api/v1/projects/{project_id}/fixer/config` (PUT requires QA Lead):

```json
{
  "enabled": false,
  "mode": "shadow",
  "runner": {"type": "none", "runner_image": null, "command_template": null, "workflow_ref": null},
  "test_globs": ["tests/**", "**/*.spec.*", "**/*.test.*"],
  "budgets": {"max_tests_per_run": 3, "max_attempts_per_test": 2, "validation_reruns": 5, "max_concurrent_open_prs": 2},
  "schedule": "off"
}
```

- **test_globs** — a generated diff may touch ONLY these paths. Anything else is
  rejected structurally *before* any code runs (`rejected_globs`).
- **budgets** — `validation_reruns` (M) is the number of sandbox reruns; a fix is
  validated only if **all M pass**. `max_concurrent_open_prs` caps how many
  fixer PRs stay open at once.
- **schedule** — `daily` / `weekly` / `off`. Runs also start manually via
  `POST /api/v1/projects/{project_id}/fixer/run`.

## Runners

| type | where it runs | notes |
|------|---------------|-------|
| `none` (default) | — | always errors "no runner configured"; never opens a PR |
| `docker` | the worker | ephemeral container per rerun: shallow clone at ref (scoped token in the clone command only), apply patch, run M times, destroy workspace. CPU/mem/timeout limits, `--network=none` by default, non-root user, no secret/DB mounts |
| `workflow_dispatch` | GitHub Actions | dispatches a reference workflow and polls the run conclusion |

For `docker`, set `runner_image` (e.g. `python:3.11`) and `command_template`
(e.g. `pytest -x {test_selector}`). The selector is passed as a single argv
element — never interpolated into a shell string.

For `workflow_dispatch`, add the reference workflow at
[`reference/testlookup-fixer-validate.yml`](reference/testlookup-fixer-validate.yml)
to `.github/workflows/` and set `runner.workflow_ref` to its filename.

## Attempts & outcomes

Every candidate produces a `fix_attempts` row you can poll at
`GET /api/v1/projects/{project_id}/fixer/attempts`; a single attempt (with the
patch + validation log digest + ledger link) is at
`GET /api/v1/fixer/attempts/{attempt_id}`. Statuses:

`selected → diagnosing → generating → validating →`
`validated | rejected_globs | failed_validation | pr_opened | error | skipped_budget`

Merged/closed states of fixer PRs are polled on a beat and fed back into the
learning loop (`record_fix_outcome`: merged → `fixed`, closed-unmerged →
`not_fixed`), the same signal a human fix produces.

## Offline

`AI_OFFLINE_MODE` (the default) is a hard gate: no fix is generated, no PR is
opened, and the run records honestly that it did nothing.
