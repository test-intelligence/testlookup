# Pass 2 — Cross-file integration checks

## SDK/CLI parity

The same `client/tests/test_commit_range.py` parameterizes assertions over the SDK and CLI modules. Before the fix, both returned the parent checkout's history for a plain nested directory; after the fix, both return `None` and diagnose `not_a_git_checkout`.

## Ingest contract

The reporter and CLI upload paths call `resolve_commit_range`, which delegates to `collect_commit_range` and preserves only the boundary-carrying wire shape. The fix is below serialization and therefore applies consistently to both upload paths without changing payload field names or precedence.

## Unchanged contracts verified

- User/env/config/CI base precedence remains covered.
- Shallow clones still degrade to head-only collection.
- Missing Git, timeout, fatal exit, and signal termination remain best-effort and non-fatal.
- Frontend tests/build/lint/theme/bundle checks pass, confirming no cross-layer regression from the client-only change.

## Runtime limitation

Docker is not installed, so API-backed or state-changing UAT flows could not be exercised locally. Those remain an explicit follow-up rather than an inferred pass.

