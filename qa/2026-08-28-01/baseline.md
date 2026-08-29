# Baseline — 2026-08-28-01

Recorded before source changes on `main` at `e230a81c`.

## Source and deployment

- Worktree: clean; branch `main`, aligned with `origin/main`.
- Homelab: three K3s nodes Ready; TestLookup workloads Ready.
- Deployed image tag: `build-20260829-002841`.
- `/health/live`: HTTP 200.
- `/health/ready`: HTTP 200; PostgreSQL, MongoDB, and Redis healthy.
- `/health/version`: HTTP 200, but build revision/date reported `unknown`.
- Local Docker CLI: unavailable. Homelab access used the repository-bundled `.codex-tools/kubectl.exe`.

## Code gates

- `scripts/quality_gate.py`: PASS (30 guards).
- Frontend lint: PASS (0 errors, 18 warnings).
- Frontend type-check: PASS.
- Frontend production build: PASS (Vite chunk-size warnings only).
- Frontend full unit suite: FAIL — 1,121 passed, 1 failed in 164 files. The isolated failing Docs test passed when run alone.
- Backend full non-integration suite: FAIL — 7,303 passed, 13 failed, 14 skipped, 5 errors.

## Failure classification at baseline

### Confirmed product defects

- `TL-2026-08-28-01-001`: DB-resolved Slack webhook is ignored by `probe_slack` when a bot-token setting is present; isolated regression test returns `auth_error`.
- `TL-2026-08-28-01-002`: `CORS_ORIGINS_RAW` constructor input is overridden by the environment alias, so `public_base_url` cannot use the documented fallback in this path.

### Environment / harness failures

- Windows Git-Bash subprocess tests cannot find `dirname` or `cat` because the test subprocess inherits an incomplete Bash PATH. This affects quickstart-env and deployment-script regression tests.
- `install.sh` syntax test invokes the WSL launcher (`C:\\Windows\\System32\\bash.exe`) and receives a non-shell UTF-16 failure; this is a host-tool selection issue.

### Test-suite stability observation

- `frontend/src/pages/DocsPage.test.tsx` copy-control reset test timed out only during the full suite and passed in isolation. No product fix is authorized from this evidence alone; it remains a gate to reproduce after confirmed fixes.
