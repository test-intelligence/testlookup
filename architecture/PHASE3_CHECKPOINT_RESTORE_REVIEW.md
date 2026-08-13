# Phase 3 Checkpoint Restore Review

## Scope

Reviewed the Phase 3 checkpoint restore hardening slice:

- `backend/app/agents/workflow.py`
- `backend/app/agents/state.py`
- `backend/tests/test_decision_evidence_checkpoint_resume.py`
- `architecture/AGENTIC_RUNTIME.md`

Goal: move Phase 3 durable resume toward authority-bound replay by restoring prior stage checkpoints only when the persisted replay metadata proves the checkpoint body has not changed.

## Findings

| ID | Severity | File | Line | Finding | Impact | Recommended fix |
| --- | --- | --- | ---: | --- | --- | --- |
| None | - | - | - | No release-blocking or correctness findings found in this scoped review. | The change is intentionally conservative: bad checkpoints are rerun rather than restored. | Keep broader same-pipeline retry/resume work as a later Phase 3 gate. |

## Pass 1: Per-File Review

`workflow.py` now validates checkpoint restore authority through `_checkpoint_restore_metadata`: `_replay.output_checksum_sha256` must match the current `checkpoint_data`, and runtime-version metadata must exist. Pipeline-bound terminal stages remain excluded from cross-pipeline restore. Invalid rows are skipped and rerun, which is the right failure mode for trust and availability.

`state.py` now declares `_checkpoint_stages` and `_checkpoint_replay_metadata`, matching the execution state already used by the graph and preserving replay breadcrumbs through final pipeline metadata.

`test_decision_evidence_checkpoint_resume.py` covers valid restore, missing replay metadata, checksum mismatch, and continued rerun of snapshot-bound terminal stages.

`AGENTIC_RUNTIME.md` now documents the checksum-authorized restore boundary and keeps the remaining Phase 3 resume/idempotency gate explicit.

## Pass 2: Cross-File Integration Review

The write path and read path now align: `_checkpoint_stage` writes `_replay.output_checksum_sha256`, `_load_checkpoint` verifies the same value before restoring, and `_mark_pipeline_done` persists `checkpoint_replay_metadata` for audit. This closes the trust gap where a stale or manually changed `checkpoint_data` body could be merged into a new pipeline attempt.

The implementation does not introduce a new migration, endpoint shape, provider call path, or deployment manifest change. It is backward-compatible because legacy checkpoints are ignored and recomputed.

## Validation

- `uv run pytest -q backend/tests/test_decision_evidence_checkpoint_resume.py backend/tests/test_agent_pipeline_hardening_regression.py`: 41 passed.
- `uv run ruff check backend/app/agents/workflow.py backend/app/agents/state.py backend/tests/test_decision_evidence_checkpoint_resume.py`: passed.
- `git diff --check` on the touched slice: passed.

## Open Questions

- Full same-pipeline resume/retry is still intentionally incomplete. The next Phase 3 slice should add durable completed-attempt idempotency and replay-safe checkpoint claiming rather than relying only on cross-pipeline restore.