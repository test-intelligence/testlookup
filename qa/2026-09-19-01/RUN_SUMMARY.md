# JR-02 → JR-06 — journey missions, slice 2 of the EXJ program

Branch `qa/journeys-jr02-jr06-2026-09-19`, cut from `main` @ `44cfc390`.
Measured against the **local stack**, not the homelab: JR-03 and JR-06 call for
killing the LLM provider and creating release policies, and those are mutations
that should not be inflicted on a live deployment. Local also gives direct SQL,
which JR-05 step 1 requires.

## Mission status — 2 of 5 partial, 1 blocked, 1 not started, 1 partial

| Mission | Status | Measured |
|---|---|---|
| [JR-02](journeys/JR-02.md) Test-run | **partial** | steps 3-4 end-to-end; 1, 2, 5 not measured |
| [JR-03](journeys/JR-03.md) AI pipeline | **blocked** | nothing about the pipeline — beat cannot reach Redis |
| [JR-04](journeys/JR-04.md) AI report | **not started** | depends on JR-03 |
| [JR-05](journeys/JR-05.md) Summary report | **partial** | steps 1-2 pass, independently re-derived; 3-6 not measured |
| [JR-06](journeys/JR-06.md) Release gate | **partial** | fail-closed layering verified; policy/band matrix and override not measured |

This is a slice, not the five journeys. Roughly half of the declared steps were
measured. The unmeasured half is enumerated per mission rather than summarised,
so nobody reads this as broader coverage than it is.

## Defects — 3 found, 3 fixed

| ID | Sev | Summary | Status |
|---|---|---|---|
| [001](defects/TL-2026-09-19-01-001.md) | S3 | Both demo seeders stored a pass rate the product calls wrong | **Fixed** |
| [002](defects/TL-2026-09-19-01-002.md) | S3 | `/settings` sub-pages had three ways back, one of which was none | **Fixed** |
| [003](defects/TL-2026-09-19-01-003.md) | S3 | `CONDITIONAL_GO` with empty `conditions_for_go`; the UI hides the section | **Fixed** |

Plus **BUG-010** filed and deliberately not fixed: the AI-pipeline debouncer is
dead code whose config describes it as live. Another worktree is actively wiring
it, so deleting it here would conflict with live work.

002 was reported by the user mid-session, not found by a mission.

## The thing worth reading — I nearly filed a false S2

JR-03 step 1 is "confirm the pipeline auto-triggers on run completion". No
pipeline appeared for the ingested run. The obvious write-up — "the AI pipeline
never auto-triggers, S2" — was wrong, and four separate checks were needed to
know that:

1. The outbox held an `agent_pipeline` row, `pending`, **`attempts=0`**. Zero
   attempts means the relay never *tried*, which points away from relay logic.
2. `relay-run-downstream-outbox` **is** scheduled and had fired 279 times. An
   earlier `head -30` of the beat task list truncated just before that entry and
   briefly suggested it was missing — my instrument, not a finding.
3. The worker had received nothing for 23 minutes; queue depths 0; brokers
   identical, so not a shard mismatch.
4. Beat's own log: `Connection to Redis lost: Retry (18/20)`, while Redis itself
   answered PONG with 55 clients. Beat logs "Sending due task", then the publish
   fails.

Then, rather than infer the relay was fine, `claim_downstream_dispatches` was
invoked in-process: it claimed all 20 pending rows and set them to `sending`
correctly, and the transaction was rolled back so no lease was consumed.

**The relay works; the broker does not.** So JR-03 measured nothing about the
pipeline, and says so, instead of converting a broken local environment into a
product defect.

## Every fix was mutation-verified, and one mutation exposed my own bad test

| Fix | Mutation | Result |
|---|---|---|
| 001 seeder rule | restore `passed / total` in one seeder | exactly 2 guards fail, for that module only |
| 002 settings back bar | delete `<SettingsBackBar />` from `AppLayout` | the wiring test fails |
| 002 settings back bar | reintroduce a page-level `<Link to="/settings">` | the "no page re-implements it" test fails |
| 003 gate conditions | restore `conditions_for_go=[]` | only the "actually uses it" guard fails, 8 pass |

The second row is the one that mattered. Before I added the `AppLayout` wiring
test, **all 10 other tests passed with `AppLayout` gutted** — every case mounts
the component directly, so the suite could not see that no page rendered it. That
is the original defect with a green suite. The same shape appears in 003: eight
tests exercise the helper in isolation and would pass with it wired to nothing.

A guard's own presence checks earned their place too: the seeder test's
"the seeder files are present" case fired immediately and caught a wrong
`parents[2]` in my first draft, which would otherwise have made the source guard
pass vacuously against two files it could not find.

## Verified numbers

- Fresh ingest through `POST /api/v1/ingest/file` — 6 passed / 2 failed / 2
  skipped → stored `pass_rate=75` (canonical 6/8), not 60 (6/10), and
  `duration_ms=4100`, exactly the sum of the per-case times. The second value
  independently re-confirms TL-2026-09-18-01-005 in a different environment.
- Seeder divergence: **28 of 294** runs, all 28 with skips, **0** without —
  the control that identified the denominator.
- Summary report: independent SQL reproduced all seven published values exactly.
- Frontend: **249 files / 1839 tests**, 0 failures (baseline 248 / 1827 — the
  delta is exactly this branch).
- Backend: 47 pass-rate tests, 81 release-gate/council tests, 12 seeder tests.
- `ruff check app/ tests/` clean · `tsc --noEmit` clean · `eslint` 0 errors, 0
  warnings on changed files · `scripts/quality_gate.py` all guards passed.

## Verified on the homelab

Deployed from a **detached worktree** at `b8302001`, not from the working tree:
`require_clean_build_inputs` correctly refuses the main tree, which still holds 22
modified source files from an interrupted mutation-test run. Build
`build-20260919-043402`, revision `b8302001`, deploy exit 0.

`frontend/tests/probe-jr-homelab-verify.spec.ts` was run **before** the deploy and
failed both checks. That is what makes the later passes evidence rather than
decoration.

```
                         before deploy                          after
settings missing     all 23 sub-pages                           []
stale Back button    /settings/ai, /settings/storage             []
duplicated                          []                          []
release gate         CONDITIONAL_GO composite=13 conditions=0    conditions=2
```

All **23** `/settings/*` sub-pages are checked, not a sample — the request was
consistency across all pages, and the three pre-fix behaviours were spread
unevenly enough that which pages you sample changes the answer.

The gate check also asserts the UI *renders* the conditions, not merely that the
API returns them: the page body contains "not measured" after the deploy.

TL-2026-09-19-01-001 (the seeder rule) is **not** verifiable live and is not
claimed to be. Deploying does not re-seed, and the homelab's existing rows keep
the values they were written with.

### The probe's first draft was wrong, and its own output caught it

It located `a[href="/settings"]` unscoped and reported `missing=[]` against a
deployment where 14 of 23 pages had nothing — because the **sidebar** carries its
own Settings nav link with that exact href. It was measuring the sidebar and
calling it a back affordance.

Left alone it would have been worse than useless rather than merely weak: after
the fix every page would have matched twice and the duplicate assertion would
have **failed a correct deployment**. Now scoped to `nav[aria-label="Breadcrumb"]`,
with a separate check that `a.btn-secondary[href="/settings"]` is gone.

That is the fifth time in this program a measurement instrument was wrong before
the subject was.

## Not done

- **Not deployed to the homelab**, so none of these three fixes is verified live.
  The settings change is layout-level and covered by a route-contract test, but
  "renders in jsdom" is not "renders in the deployed bundle".
- JR-03/JR-04 need beat→Redis fixed on the local stack before they can start.
- The 28 already-seeded rows keep their wrong `pass_rate`; the generator is
  fixed, existing data is not. A backfill would rewrite data the owner may be
  comparing against, so it is a decision, not an oversight.
- JR-05's temporal split, PDF export and shared-report scope, and JR-06's band
  boundaries and override audit, are the highest-value remaining items.
