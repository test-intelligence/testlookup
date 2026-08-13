# Per-file Analysis

## PR #335

- `frontend/package.json`: changes the dev-only Prettier range from `^3.4.2`
  to `^3.9.4`; locally coherent.
- `frontend/package-lock.json`: resolves Prettier 3.9.4 with matching
  integrity metadata; technically valid but stale and superseded by #569.

## PR #569

- `frontend/package.json`: raises the dev-only Prettier range to `^3.9.4`.
- `frontend/package-lock.json`: resolves Prettier 3.9.6 and updates only the
  expected package metadata.
- `CHANGELOG.md`: documents the supersession and validation. It overlaps the
  same top insertion area as the other feature PRs.

## PR #568

- `FirstRunGuide.tsx`: accepts `projectId` and builds steps through the helper;
  component behavior remains presentational and scoped.
- `firstRunSteps.ts`: centralizes command construction and preserves the
  placeholder for All Projects mode. Trim or validate the emitted ID (P2).
- `FirstRunGuide.test.tsx`: covers scoped, unscoped, copied, and helper-level
  behavior; good local coverage.
- `OverviewPage.tsx`: passes `project?.id` only when the dashboard is scoped;
  correct data flow.
- `CHANGELOG.md`: accurate but conflicts at integration time.

## PR #570

- `health.py`: adds a dependency-free `/health/version` route using existing
  build provenance and uptime helpers. Imports and response shape are locally
  coherent.
- `test_health_build_provenance.py`: verifies payload and no dependency probes;
  add an HTTP route-registration assertion (P2).
- `CHANGELOG.md`: documents the endpoint and its probe contract; conflicts at
  integration time.

## PR #571

- `client.py`: maps `httpx.RequestError` for normal requests and downloads;
  preserves HTTP status mapping.
- `upload.py`: maps transport failures for direct file upload; status failures
  still use a generic exception (P2 consistency gap).
- `errors.py`: maps timeout versus connection failures with actionable text;
  redact the URL before display (P2 privacy hardening).
- `test_client_connection_errors.py`: covers connect, timeout, download,
  upload, and status-code paths; strong regression coverage.
- `CHANGELOG.md`: documents the behavior; conflicts at integration time.

