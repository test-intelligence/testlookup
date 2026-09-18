# EXJ-2026-09-18 — Run Summary

Full-system exploratory + E2E journey validation.
Branch `qa/exploratory-e2e-2026-09-18`, cut from `main` @ `44be1f20`.

> **Status: Phase 1 partial.** Phases 4–6 (branch review, homelab deploy and
> verification, single PR and merge) have **not** run. Nothing is pushed.

## Defects

| ID | Sev | Summary | Status |
|---|---|---|---|
| [001](defects/TL-2026-09-18-01-001.md) | S2 | Three probe sweeps asserted against `/overview`, not the pages they named | FIX READY |
| [002](defects/TL-2026-09-18-01-002.md) | S2 | A leftover pytest scratch directory failed the mcp image build | FIX READY |
| [003](defects/TL-2026-09-18-01-003.md) | S2 | The release KPI strip stated four numbers it never counted | FIX READY |
| [004](defects/TL-2026-09-18-01-004.md) | S3 | Four release controls only toast "coming in next iteration" | **OPEN — owner decision** |
| [005](defects/TL-2026-09-18-01-005.md) | S2 | Every ingested run carried a NULL duration | FIX READY |
| [006](defects/TL-2026-09-18-01-006.md) | S2/P0 | The cross-tenant guard stopped reading request bodies, and stayed green | FIX READY |

Five fixed, each with a regression test **in a tier CI actually executes**, each
verified red-before / green-after, each mutation-verified. One left open because
it needs a product decision this program should not make alone.

### Every finding was a measurement that had stopped measuring

- **001** — three sweeps passing while never opening their subjects.
- **003** — seven of eight points in every KPI trend line were literals.
- **005** — a duration parsed, stored per case, dropped at the aggregate; hidden
  because the seeded demo data filled the column from elsewhere.
- **006** — a cross-tenant scan that stopped reading request bodies and reported
  zero offenders, which looks exactly like a clean codebase.

That is the pattern the plan predicted, and it held on the first day.

## Baseline (Gate B0)

| Gate | Result |
|---|---|
| Source | tree identical to `origin/main` @ `44be1f20` |
| Declared routes | 69 (36 app + 31 management + 2 auth), derived from `App.tsx` |
| Quality gate | 43 guards passed (44 after this program's guard) |
| Frontend unit | **245 files, 1815 tests, 0 failures** (312.8 s) |
| Frontend lint | 0 errors, 18 warnings (pre-existing) |
| `tsc --noEmit` | clean |
| Backend unit (host) | **24 failed, 11,946 passed, 448 skipped** (1 h 15 m) |
| Local stack | 11 containers, alembic `0191 (head)` |
| Homelab | live, verified **by body**, not status code |

### The backend baseline needed three attempts to become a real number

- **In-container** (`make test-backend`): 310 failed / 11,229 passed. Nearly all
  of it environmental — 374 `FileNotFoundError` for repo-root files the image
  does not carry, and 153 `OSError: could not get source code`, which kills every
  source-text assertion test this repo relies on.
- **On host, first try**: 6 collection errors, all one missing package (`pyotp`).
- **On host, complete**: the number above.

Proved the in-container failures were environmental rather than assuming it: two
modules that failed there were run on the host and **24 passed**.

Of the 24 host failures, 20 are environmental — `chromadb` and `docx` absent, a
`langchain.agents.create_react_agent` version skew, and `WinError 5` on the same
permission-locked scratch dirs as defect 002. One was real, and became 006.

## Missions

| Mission | Outcome |
|---|---|
| EX-01 Landmark tour | **Partial.** Instrument rebuilt five times; see below |
| EX-03 Interface tour | Partial — found 003 and 004 |
| [EX-05](exploratory/EX-05.md) Authorization, 5 roles, server-side | **No defect.** Holds on every endpoint tested |
| [EX-06](exploratory/EX-06.md) Tenancy leak hunt | **No defect.** No leak on any surface tested |
| [JR-01](journeys/JR-01.md) Ingestion journey | Hop 1 complete — found 005 |
| JR-02 … JR-10 | **Not started** |

## Four times the instrument was wrong, and that is the main finding

Every one of these would have produced a false report if it had gone unchecked.

1. **The authorization matrix reported a total breach that did not exist.** First
   run: 200 for every role on every endpoint, including `/users` for VIEWER.
   `dev_login` declares `role` and `username` as bare scalars, so FastAPI treats
   them as **query** parameters and ignored the JSON body being sent — every
   call fell through to `role="admin"`. The tell was one check: all five tokens
   carried the same `sub`, and `/auth/me` returned `admin/ADMIN` five times.
2. **The write matrix poisoned its own later rows.** ADMIN's `PATCH
   /users/{id}/role` succeeded against the tester in an earlier iteration, so by
   the TESTER row that account really was an admin — it cleared
   `require_role(ADMIN)` and hit the handler's own "cannot change your own role"
   400. A 403 that looked inconsistent was sequencing.
3. **A new test was vacuous, and then a mutation survived.** The first KpiStrip
   test asserted a real `0` does not render as the `|| 6` fallback — but that
   fallback only ever affected the sparkline, so it would have passed against
   the buggy code. Replaced; then a second mutation (deriving `readyToShip` from
   `releases` instead of `inProgress`) **survived**, because the fixture's only
   `go` release was in progress and could not tell the two apart.
4. **The guard's own error message invited the wrong fix, and it was made
   first.** 006's failure says "Delete them — the backlog only shrinks". Deleting
   the exemption made the module green. Mutation testing caught it: removing the
   handler's scope resolution, which should have made a genuinely-scoped route
   reappear as an offender, changed nothing — the route was never reaching the
   check.

### EX-01's instrument, five iterations

Recorded because the failure modes are reusable, and because two of them
produced findings that were not real:

| Attempt | Failure | Cost |
|---|---|---|
| 1 | Context per route | Browser exhausted at ~57 contexts |
| 2 | Context per 12 routes | ~2 min/route; died on the 20-minute cap with every row buffered |
| 3 | One page, `networkidle` | The neighbouring probe's header says pollers mean it never settles. ~2 min/route again |
| 4 | One page, content wait | **2.6 s/route.** Renderer crashed at route 9 |
| 5 | Context recycled every 6 routes | A fresh *page* shares the renderer process; only a fresh context gets a new one |

Two false findings came out of it and neither was filed:

- `/getting-started` flagged **THIN (386 chars)** by the broken version; with a
  correct content wait it reads 1450.
- `/coverage/suite` blamed for the renderer crash. Cleared twice — loaded alone
  it renders 479 chars with no errors, and `/coverage → /coverage/suite →
  /suites` in sequence is clean. The limit is cumulative.

## Live-mutation cleanup — all reverted and verified

- `tester` role restored `ADMIN` → `TESTER`; all five seeded users back to their
  original roles.
- Projects `exj-authz-probe` and `exj-tenancy-b` deleted; `/projects` shows only
  the three seeded projects.
- Synthetic `qalead-*` users left behind are `is_active=false` with no
  memberships — verified as correct soft-delete behaviour, not leaks.

## Not measured — stated so nothing here reads as more than it is

- JR-02 … JR-10, and the JR-01→JR-06 spine spec the plan makes the headline
  deliverable. **Not started.**
- JR-01 covered **one parser on one path**. The JSON batch endpoint, the CLI,
  the Python/Java SDKs and the other ten parsers are untouched.
- EX-05/EX-06 did not probe ids in **request bodies or headers**, cross-tenant
  **writes**, or API-key auth as a second path onto the same data.
- EX-01 swept at most 18 of 56 static routes before the harness limit.
- Whether CI is currently red on `main` for 006 was **not verified** — CI has not
  been run in this program.
- No homelab deploy, no verification against it, no PR.
