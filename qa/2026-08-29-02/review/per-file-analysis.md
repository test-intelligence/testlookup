# Pass 1 — Per-file analysis

## `client/commit_range.py`

- Responsibility: collect and diagnose local Git commit ranges for the Python reporter SDK.
- Inputs/outputs: explicit/config/CI base refs plus optional `repo_path`; returns a bounded wire payload or a diagnostic outcome.
- Local finding: `git -C <path>` searched parent directories when `repo_path` was a plain nested directory, violating the explicit-path contract.
- Security/data integrity: this could attribute commits from an unrelated parent checkout to the current ingest.
- Tests: the existing negative-path and diagnostic tests exposed the defect; positive precedence, shallow clone, cap, and Git failure tests remained relevant.
- Action: fixed with an exact checkout-root guard; no new test file needed.

## `cli/testlookup_cli/commit_range.py`

- Responsibility: CLI copy of the same commit-range collector contract.
- Local finding: identical upward-search behavior and identical failing regression assertions.
- Action: applied the same guard to keep CLI and SDK behavior aligned.

## Test surfaces

- `client/tests/test_commit_range.py` is the shared high-value regression surface and imports both implementations.
- `cli/tests/test_upload_commit_range.py` validates CLI upload wiring and passed in the full CLI/SDK rerun.
- No frontend or backend files were changed by this run; their current checks were used as regression evidence.

