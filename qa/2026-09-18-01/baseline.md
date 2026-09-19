# Baseline — EXJ-2026-09-18 (run `2026-09-18-01`)

Recorded by ORCH before any mission. Gate B0 asks for a green baseline with
**recorded numbers**, not the word "green".

## Source

| Fact | Value |
|---|---|
| Branch | `qa/exploratory-e2e-2026-09-18` |
| Cut from | `main` @ `44be1f20` |
| Tree vs `origin/main` | identical (`git diff --stat origin/main` empty) |
| Plan | `docs/EXPLORATORY_E2E_MASTER_PLAN_2026_09_18.md` (gitignored, local-only) |

## Declared surface (derived, not retyped)

Parsed from `frontend/src/App.tsx` into `evidence/route-inventory.json`:

| Count | What |
|---|---|
| 36 | `appRoutes` (authenticated) |
| 31 | `managementRoutes` (QA_LEAD / ADMIN, `ManagementGuard`) |
| 2 | `/login`, `/reset-password` |
| **69** | total declared |
| 58 | static (no `:param`) |
| 11 | dynamic |

Backend: 90 routers, 289 services, 30 agents, 11 parsers. Roles: `VIEWER`,
`TESTER`, `QA_ENGINEER`, `QA_LEAD`, `ADMIN`.

## Environments

### LOCAL — `make dev`

`make dev` **failed** at first run; see `defects/TL-2026-09-18-01-002.md`. After
that fix the stack came up from pre-built images.

| Check | Result |
|---|---|
| Containers running | 11 |
| postgres / redis / mongo / minio / backend | `Up (healthy)` |
| worker / worker-children / beat / flower / frontend / mcp | `Up` |
| Alembic | `0191 (head)` |
| `http://localhost:3000/` | 200 |
| `http://localhost:8000/api-docs` | 200 |
| `http://localhost:8000/api/v1/projects` | 401 — mounted, guard ran |
| `http://localhost:8000/health` | 404 — expected; API health is not at `/health` |

### HOMELAB — `http://testlookup.local`

| Check | Result |
|---|---|
| Reachability | 200, **body verified** as the TestLookup SPA, not SignalForge |
| Admin login | `admin` / form-encoded, succeeded |
| Route sweep (18 curated) | 18/18 pass |
| Exploratory walk (18 pages) | 18/18 render |

Traefik `192.168.0.201` is shared with SignalForge and routes by Host header, so
every check above was made with `-H "Host: testlookup.local"` and judged on the
response body.

## Gates

| Gate | Command | Result |
|---|---|---|
| Quality gate | `python scripts/quality_gate.py` | **43 guards, passed** (baseline); 44 after this program's guard |
| Frontend unit | `npx vitest run` | **245 files, 1815 tests, 0 failures**, 312.8 s |
| Frontend lint | included in `frontend-test` | not yet run at baseline |
| Backend unit | see below | **could not be run cleanly in either environment** |

### Backend suite — measured, and not green in either environment

This is the one baseline number the program does **not** have, and the reason is
recorded rather than rounded off.

| Environment | Command | Outcome |
|---|---|---|
| In container | `docker compose exec backend pytest tests/` | 14 collection errors, `FileNotFoundError` |
| On host | `cd backend && pytest tests/ -p no:testlookup` | 12,245 tests collected, 6 collection errors |

Both are environment gaps, not regressions:

- **In container:** the failing modules read repo-root files that the image does
  not carry — e.g. `tests/test_smoke_script.py` wants `/scripts/smoke.py`.
  `make test-backend` runs in-container; CI's `backend-test` job runs on the
  host with `working-directory: ./backend`, so CI never meets these.
- **On host:** `ModuleNotFoundError: No module named 'pyotp'` and five siblings —
  the host interpreter does not have `backend/requirements.txt` installed. CI
  installs it.

A full run with the 14 path-dependent modules excluded was started to get a real
number; whatever it reports is recorded in `RUN_SUMMARY.md`. Until then the
backend baseline is **`measured: false`** — not "green", and not 0.

`-p no:testlookup` is required on the host: the installed reporter plugin
crashes on a missing `ci_context`.

## Gate B0 verdict

**PASS with one stated exception.** Source, surface, both environments, the
quality gate and the frontend suite are all measured and green. The backend
suite is unmeasured for environment reasons that are named above and that CI
does not share. Missions may proceed; no backend claim in this program may cite
a baseline it does not have.

## Defects found while establishing the baseline

| ID | Severity | Summary | Status |
|---|---|---|---|
| TL-2026-09-18-01-001 | S2 | Three probe sweeps assert against `/overview`, not the pages they name | FIX READY |
| TL-2026-09-18-01-002 | S2 | A leftover pytest scratch directory fails the mcp image build | FIX READY |

Both were found before any mission started — the baseline itself was the first
productive test.
