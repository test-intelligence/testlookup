# Open Pull Request Review — 2026-08-13

## Scope

Repository: `anandtopu/testlookup`

Reviewed open PRs #335, #568, #569, #570, and #571 against `main`, including
metadata, changed-file patches, CI workflow runs, review threads, and cross-PR
overlap.

## Verdict

Four PRs are technically ready for merge based on the current evidence:

- #569 Prettier 3.9.6 dependency refresh
- #568 scoped first-run upload command
- #570 cheap build-identity health endpoint
- #571 actionable CLI transport errors

PR #335 is stale and non-mergeable. It is fully superseded by #569 and should
be closed as superseded, not merged.

## Findings

### P1 — Merge-order conflict across shared changelog

PRs #568, #569, #570, and #571 all modify the same top section of
`CHANGELOG.md`. Their current GitHub mergeability is evaluated against the old
base and does not prove that the later PRs will remain conflict-free after the
first merge. Merge #569 first, then resolve the changelog insertion conflicts
for #568, #570, and #571 while preserving every dated entry.

### P2 — #570 endpoint test does not verify HTTP route registration

`backend/tests/test_health_build_provenance.py` calls `health.version()`
directly. That proves the function payload and no-probe behavior, but not that
the `/health/version` route is mounted in the application router. Add one
TestClient/router-level assertion for the actual path before relying on this as
a deployment smoke contract.

### P2 — #571 transport error text can echo a configured URL verbatim

`map_connection_error()` includes the complete `base_url` in the CLI error.
Profiles and `TESTLOOKUP_URL` are user-controlled strings; a URL containing
userinfo, query credentials, or accidental tokens could be echoed to the
terminal or CI logs. Redact userinfo/query/fragment components before display,
and add a canary URL regression test.

### P2 — #568 helper checks a trimmed ID but emits the untrimmed value

`uploadCommand()` uses `projectId.trim()` only as a non-empty check, then
interpolates the original string. Backend UUIDs are normally clean, so this is
not a current production blocker, but whitespace supplied by a mocked or
future source creates a malformed copy command. Emit the trimmed value or
validate the UUID shape.

### P2 — #571 upload status errors remain inconsistent

The shared client maps HTTP status failures through `map_http_error`, while the
direct upload path still raises a generic `Exception` for HTTP >= 400. The PR
fixes transport failures on that path, but users can still receive different
error UX depending on command. Normalize the upload status branch in a follow-up
or explicitly document it as out of scope.

## Recommended merge order

1. Merge #569 (the authoritative Prettier update).
2. Close #335 as superseded.
3. Merge #568, resolving its `CHANGELOG.md` hunk if needed.
4. Merge #570, resolving its `CHANGELOG.md` hunk if needed.
5. Merge #571, resolving its `CHANGELOG.md` hunk if needed.
6. Run the protected backend/frontend/CLI checks on the resulting `main`.

## Evidence

- #335: open, `mergeable=false`, CI run 736 successful.
- #568: open, `mergeable=true`, CI run 1155 successful.
- #569: open, `mergeable=true`, CI run 1156 successful.
- #570: open, `mergeable=true`, CI run 1157 successful.
- #571: open, `mergeable=true`, CI run 1158 successful.
- No submitted reviews or inline review threads were present on any PR.

