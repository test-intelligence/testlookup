# Triaging failures

Once results are flowing in ([Getting results in](getting-results-in.md)), triage is the daily loop: understand what broke, decide what it is, and route it to an outcome. TestLookup spreads this across three pages, each answering a different question:

| Page | Question it answers |
|---|---|
| **Failure Analysis** (`/failures`) | *What is failing across the project, and why?* — clusters, categories, verdicts, trends |
| **My Failures** (`/my-failures`) | *What is assigned to me right now?* — your personal triage inbox |
| **Run detail** (`/runs/:id`) | *What happened in this specific run?* — per-test errors, steps, history |

## Reading the Failure Analysis page

`/failures` is the project-wide view for a chosen time window:

- **Failure metrics & timeline** — failure counts, pass rate, mean time to fix, and how the current window compares to the previous one.
- **Failure clusters** — failures whose error signatures share a root cause are grouped, so one broken dependency reads as one cluster, not thirty red tests. Start triage at the cluster level; fixing the cause clears the whole group.
- **Failure category distribution** — every analyzed failure gets a category (product defect, automation/script issue, environment, flaky, …) from the analysis pipeline (rules first, then ML/LLM when enabled). "Category unknown" entries can be sent through classification with **Classify uncategorised failures**.
- **Failure verdict** — for a selected test, the evidence-backed verdict: failure rate, whether it looks like a *regression* (same error, consistent) or *flaky* (alternating, varied signatures), and its likely cause.
- Per-test actions include **Open triage queue** (jump to the inbox filtered to this window), **Mute the test with a documented reason**, and **Export failure data as CSV**.

> A test that fails *consistently with the same error* is treated as a regression — expect "fix it" guidance. Alternating pass/fail with varied errors points to flaky — see [the flaky workflow](#when-its-flaky) below.

## Your inbox: My Failures

New failures are **auto-assigned** — to the suite's owner if one is configured (see `/ownership`), otherwise to a QA engineer — and land in the assignee's `/my-failures` inbox with status `PENDING_REVIEW`. Only `PENDING_REVIEW` rows show; picking any resolution status drops the row off the inbox.

For each failure you can:

1. **Open it** (click the row) — goes to the test's run-detail view with the error, stack trace, steps, and cross-run history.
2. **Reassign** — to the suite owner or another QA engineer, when it landed on the wrong desk.
3. **Resolve** — pick the outcome that matches what you found:

| Status | Meaning |
|---|---|
| `REVIEWED_APPROVED` | Looked at it; the failure is understood/expected — no further action |
| `DEFECT_CREATED` | It's a real product bug — a defect was filed (see `/defects` for promotion) |
| `WONT_FIX` | Real but deliberately not being fixed |
| `AUTOMATION_SCRIPT_ISSUE` | The *test* is broken, not the product |
| `FLAKY_TEST` | It's flaky — hand off to the flaky workflow below |

**Mine vs Team:** QA leads and admins get a scope toggle. Note that auto-assignment often lands on the project's default QA-lead user, so an admin's "Mine" view can look empty while "Team" holds the real queue — flip the toggle before concluding there's nothing to triage.

## Correcting the AI (and why it's worth doing)

Every AI analysis on a failure can be rated. If the verdict or category is wrong, mark it **incorrect and supply the right category / root-cause summary**. This does three things:

1. Fixes the current analysis record immediately.
2. Evicts the stale answer from the semantic cache so it isn't re-served to similar failures.
3. **Sticks for the future** — the next time the same logical test (same fingerprint) is analyzed, your correction is applied *before* the rules/ML/LLM run, short-circuiting the known-wrong path with a human-corrected verdict.

Corrections compound: each one permanently improves the analysis for that test. It's the highest-leverage 30 seconds in the triage loop.

## When it's flaky

If the verdict says flaky (or you picked `FLAKY_TEST`):

- **Flaky Coach** (`/flaky-coach`) shows the test's cross-run history, confidence, and — for step-based tests — *which step* flips verdict across runs ("Cross-Run Step Flakiness" on the test detail page).
- **Quarantine** (`/quarantine`) holds known-flaky tests so they stop dragging down the release signal while being fixed. The detector proposes quarantines; a human approves, rejects, or later releases them — proposals that nobody acts on expire on their own.

## Escalating to a defect

When a cluster is a real product bug, promote it from `/defects` — defect promotion carries the cluster's evidence along, and `DEFECT_CREATED` closes the inbox rows it explains.

## Tips

- **Triage clusters, not tests.** One cluster = one investigation, however many tests it covers.
- **Trust the window.** All three pages share the global time window; if numbers look off between pages, check the window picker first.
- **Empty page ≠ no failures.** Check that a project is selected (top bar) and the window isn't too narrow. Validation errors surface as toasts.
