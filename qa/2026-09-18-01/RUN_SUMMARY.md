# EXJ-2026-09-18 — Full-system exploratory + E2E journey validation

Branch `qa/exploratory-e2e-2026-09-18`, cut from `main` @ `44be1f20`.
13 commits · 43 files · deployed and verified on the homelab.

## Defects

| ID | Sev | Summary | Status |
|---|---|---|---|
| [001](defects/TL-2026-09-18-01-001.md) | S2 | Three probe sweeps asserted against `/overview`, not the pages they named | **Fixed** |
| [002](defects/TL-2026-09-18-01-002.md) | S2 | A leftover pytest scratch directory failed the mcp image build | **Fixed** |
| [003](defects/TL-2026-09-18-01-003.md) | S2 | The release KPI strip stated four numbers it never counted | **Fixed** |
| [004](defects/TL-2026-09-18-01-004.md) | S3 | Six `/releases` + `/agents` controls only toast "coming in next iteration" | Backlog — needs a product decision |
| [005](defects/TL-2026-09-18-01-005.md) | S2 | Every ingested run carried a NULL duration | **Fixed + verified live** |
| [006](defects/TL-2026-09-18-01-006.md) | S2/P0 | The cross-tenant guard stopped reading request bodies, and stayed green | **Fixed** |
| [007](defects/TL-2026-09-18-01-007.md) | S2 | `/intelligence` table: Build ate the row, Pass rate was clipped | **Fixed + verified live** |
| [008](defects/TL-2026-09-18-01-008.md) | S3 | "0 flaky" vs "1 quarantine" — traced to two different measurements | Backlog — investigation, with the queries |
| [009](defects/TL-2026-09-18-01-009.md) | S2 | Selecting a run on `/agents` left the previous run's data on screen | **Fixed** |
| [010](defects/TL-2026-09-18-01-010.md) | — | Enhancement: `/agents` report above stages, stages collapsible | **Done + verified live** |

Seven fixed, three carried to `LIVE_APP_BUG_TRACKER.md` as BUG-005 … BUG-008.
Every fix landed red-before/green-after, mutation-verified, with its regression
test **in a tier CI actually executes**.

## Every finding was a measurement that had stopped measuring

- **001** — three sweeps passing while never opening their subjects. `/failure-analysis`,
  `/tests` and `/flaky` are not declared routes, so the SPA catch-all sent all
  three to `/overview`, which *is* a page that renders.
- **003** — seven of eight points in every KPI trend line were literals, plus two
  invented captions (`avg gate 97.1%`, `100% audit-packed`).
- **005** — a duration parsed, stored per case, then dropped at the aggregate.
  `grep` for an assignment to a `TestRun`'s `duration_ms` across `backend/app`
  returned nothing. Hidden because the seeded demo data fills the column from
  elsewhere, so only real uploads were affected — and `/runs` then warned the
  user their data was incomplete.
- **006** — the scan that exists because triaging its backlog by hand once found
  **nine cross-tenant holes** had stopped reading request bodies entirely. It
  reached the Pydantic model through `field.type_`, which this FastAPI does not
  define, so `getattr` returned `None` and no body was ever scanned. It fails
  *green*.
- **007** — the pass-rate cell was 86px where its contents need 120.

## Verified on the homelab

Deployed twice from a **detached worktree** at the reviewed commits (see
"Working tree" below). Final: `build-20260918-224140`, revision `8cbbcb5e`.

The verification probe was run **before** the first deploy and failed both
checks. That is what makes the later passes evidence rather than decoration.

```
before deploy   table=946  build=507 (53.6%)  fill=100%    pass 86/108
first fix       table=860  build=384 (44.7%)  fill=90.9%   pass 120/108   <- reported by the user
final           table=946  build=378 (40.0%)  fill=100%    pass 142/108
```

The middle row is a defect this program shipped and the user caught: capping the
table stopped Build growing but left the table short of its panel, moving the
empty band to the right-hand edge. Nothing caught it because every assertion was
about the *columns* and none about the table against the space it was given. The
probe now checks panel fill in **both** directions.

`duration_ms: 12456` on a live ingest — exactly the sample suite's declared
`time="12.456"`. The run was unlinked from its release and deleted afterwards
(404 confirmed, no leftovers).

## The spine — gap G1 closed

`frontend/tests/ci-e2e/journey-spine.spec.ts` carries one failure across
`/runs → /runs/:id → /runs/:id/intelligence → /reports/summary → /release-gate/:id`.

No existing test crossed more than one hop: 45 e2e specs and 33 probes each stop
at a single page, so a run whose failure never reached the gate's reasoning was
caught by nothing. Mutating the chain to substitute a different test name at the
AI-report hop fails it.

It is in `ci-e2e/` deliberately — **CI runs only that directory**
(`playwright.ci.config.ts`, `ci.yml:242`). The 45 specs in `tests/e2e/` and the
33 `probe-*` specs never run there, so a regression test placed among them never
executes again.

## Baseline (Gate B0)

| Gate | Result |
|---|---|
| Quality gate | 43 guards passed at baseline; **44** with this branch's new guard |
| Frontend unit | 245 files / 1815 tests at baseline → **248 / 1827**, 0 failures |
| Backend unit (host) | **24 failed, 11,946 passed, 448 skipped** |
| `tsc --noEmit` | clean · **lint 0 errors** (17 warnings, one below baseline) |

The backend number took three attempts. In-container it reports 310 failures —
374 `FileNotFoundError` for repo-root files the image does not carry and 153
`OSError: could not get source code`, which kills every source-text assertion
test this repo relies on. Proved that was environmental rather than asserting
it: two modules failing there passed **24/24** on the host. Of the 24 real host
failures, 20 are missing optional deps and a langchain skew; one became 006.

## Four times the instrument was wrong

Each would have shipped a false finding unchecked. They are written into the
reports rather than quietly corrected.

1. **An authorization matrix reported a total breach that did not exist** — 200
   for every role on every endpoint, including `/users` for VIEWER. `dev_login`
   declares `role`/`username` as bare scalars, so FastAPI treats them as **query**
   parameters and ignored the JSON body; every call fell through to
   `role="admin"`. All five tokens carried the same `sub`.
2. **A write matrix poisoned its own later rows** — ADMIN promoted the tester in
   an earlier iteration, so the TESTER row ran *as an admin*.
3. **A new test was vacuous, then a mutation survived** — the KpiStrip fallback
   only ever affected the sparkline, and a fixture whose only `go` release was
   in progress could not tell `inProgress.filter(go)` from `releases.filter(go)`.
4. **A guard's own error message invited the wrong fix, and it was taken first** —
   006 says "Delete them — the backlog only shrinks". Deleting the exemption made
   the module green. Mutation testing caught it: removing the handler's scope
   resolution changed nothing, because the route was never reaching the check.

A fifth, found by the user: the `/intelligence` fix shipped a new gap, and a
sixth — the geometry test's Build-share threshold was set at `0.55` and so
**passed against the 53.8% defect it was written for**.

## EX-01's instrument, six iterations

Recorded because the failure modes are reusable and two of them produced
findings that were not real.

| Attempt | Failure |
|---|---|
| 1 | Context per route — browser exhausted at ~57 contexts |
| 2 | Context per 12 routes — ~2 min/route, died on the cap with every row buffered |
| 3 | One page, `networkidle` — the neighbouring probe's header says pollers mean it never settles |
| 4 | One page, content wait — **2.6 s/route**, renderer crashed at route 9 |
| 5 | Page recycling — a fresh page shares the renderer process |
| 6 | Context recycling + crash recovery — still hit the ceiling |

Two false findings, neither filed: `/getting-started` flagged THIN at 386 chars
(reads 1450 with a correct content wait), and `/coverage/suite` blamed for the
crash — cleared three separate ways. EX-01 is **partial**: at most 18 of 56
static routes. The renderer ceiling wants a dedicated memory-profiling mission,
not more guessing.

## Not measured — so nothing here reads as more than it is

- **JR-02 … JR-10 not started.** JR-01 covered **one parser on one path**; the
  JSON batch endpoint, the CLI, the Python/Java SDKs and the other ten parsers
  are untouched.
- EX-05/EX-06 did not probe ids in **request bodies or headers**, cross-tenant
  **writes**, or API-key auth as a second path onto the same data.
- Whether CI is currently red on `main` for 006 was **not verified** — CI was not
  run in this program. It may have come from a FastAPI/Pydantic bump whose effect
  nobody attributed.
- BUG-007 (flaky vs quarantine) is analysis from source only; no query was run
  against the deployment.

## Working tree — for the reviewer

Both deploys came from a **detached worktree** at the reviewed commits, not from
the working tree. The deploy script's `require_clean_build_inputs` refused the
main tree, correctly: it holds **six un-restored mutation-test mutations** from
an interrupted run (14:14–14:53 on 2026-09-18), including
`email_service.py` turning a `raise` into `pass` on a secret-decryption failure
and `llm_circuit_breaker.py` reporting an open breaker as `0.0`. None of it
reached the homelab and none of it is in this branch.

## Live-mutation cleanup — all reverted and verified

- `tester` role restored; all five seeded users back to their original roles.
- Local projects `exj-authz-probe` and `exj-tenancy-b` deleted.
- The homelab verification run deleted (unlinked from its release first).
- Synthetic `qalead-*` users left behind are `is_active=false` with no
  memberships — verified as correct soft-delete behaviour, not leaks.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
