# Pass 1 — Per-file checklist

Judge each file **in isolation**. Defer anything that requires looking at another file to Pass 2. Read the whole file, not just the diff hunk.

## Apply to every file

- **Correctness in the small.** Off-by-one, null/None handling, empty-collection edge cases, error paths that swallow exceptions, incorrect early returns, mutated-during-iteration, timezone/`naive datetime` mistakes.
- **Readability.** Names match surrounding code; comment density matches the file; no novel idiom where the file already has a convention. Functions do one thing.
- **Dead code / scope creep.** Commented-out code, unused imports/exports, "for future use" abstractions, refactors bundled into a fix that wasn't requested, backwards-compat shims for paths that never shipped.
- **Security in the small.** No hard-coded secrets/tokens, no `print`/`console.log` leaking sensitive data, no logging of PII before redaction, no string-formatted SQL, no `eval`/`exec` on input.
- **Error handling & logging.** Exceptions carry context; logs use the project logger. **structlog gotcha:** `logger.warning("x: %s", exc)` raises `TypeError` (BoundLogger rejects stdlib positional args) — flag any `%s`/positional logging; require kwargs.
- **Tests in the same diff.** A changed source file under `backend/app/`, `frontend/src/`, `cli/`, `mcp/`, or a client SDK with **no** matching test change is a Major finding ("untested change").

## Backend Python (`backend/app/**`)

- Async all the way: no sync DB/IO calls inside `async def`; `await db.refresh(row)` after a flush when a serializer reads an `onupdate`/`func.now()` column (else `MissingGreenlet`).
- Services must NOT call `db.rollback()` on an injected session — it aborts the caller's transaction and produces a context-free 500.
- SQLAlchemy `IN` clauses use `bindparam("x", expanding=True)` + a list, never a scalar tuple.
- Pydantic v2 enum fields backed by a `String(N)` column: a bad value yields a silent 422 (axios suppresses the toast). Flag strict enums over free-text columns.
- Router files stay thin — no business logic in a `routers/*` handler (Pass 2 confirms it lives in a service).
- Role guard present where the endpoint needs one (`require_role(UserRole.X)`); destructive endpoints require typed-name confirmation + a `settings_audit_log` write.
- New capability is gated by a feature flag, and `AI_OFFLINE_MODE` short-circuits any outbound call **before** the flag check.

## Migrations (`backend/migrations/versions/**`)

- Both `upgrade()` and `downgrade()` implemented (no `pass` downgrade).
- `down_revision` is a real revision string (Pass 2 checks it points at the true head — multiple-head conflicts are common from parallel agents).
- No data-destructive op without an explicit, intentional comment.

## Frontend (`frontend/src/**`)

- Data fetching via SWR + the single Axios instance; no bare `fetch`/new axios client.
- Permission gating via `usePermissions()` / `ManagementGuard`, not ad-hoc role string checks.
- `navigator.clipboard` not called directly — use `utils/clipboard.copyTextToClipboard` (HTTP homelab origins).
- No `ALL_PROJECTS_ID` (`"all"`) sent to the backend as a query param where a UUID is expected.
- Component reads the shared time-window store rather than re-deriving its own default window.
- Error reports scrub PII via `utils/errorReporting.ts`.

## CLI / MCP / SDK

- CLI: config precedence respected (constructor > env > `testlookup.properties` > defaults); no secret echoed to stdout.
- MCP: tool registered via the `@mcp.tool` decorator; offline-mode honored.
- SDK: wire shape matches `POST /api/v1/stream/ingest` (`{run_id, events[], meta{...}}`); final batch emits `event_type: "run_complete"`.

## Output shape per file

```
### path/to/file.py
- [SEV] path/to/file.py:42 — <title>. <problem>. Fix: <concrete action>.
- [SEV] path/to/file.py:88 — <title>. ...
```
A clean file: `### path/to/file.py — no findings.`
