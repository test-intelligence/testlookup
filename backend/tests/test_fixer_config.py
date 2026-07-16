"""Fixer config + attempt serialization + PUT-body validation + diff apply (AI-2).

Pins the pinned wire contract key-sets, the shadow/suggest/act policy layer,
and the agent_policies storage of the fixer config — all DB-free (a staging
fake session for the upsert path).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.fixer.pipeline import apply_unified_diff, split_per_file_diffs  # noqa: E402
from app.models.postgres import AgentPolicy, FixAttempt  # noqa: E402
from app.services import fixer_service as svc  # noqa: E402

PROJECT_ID = uuid.uuid4()

# Contract key-sets (frontend built against these verbatim).
CONFIG_KEYS = {"enabled", "mode", "runner", "test_globs", "budgets", "schedule"}
RUNNER_KEYS = {"type", "runner_image", "command_template", "workflow_ref"}
BUDGET_KEYS = {"max_tests_per_run", "max_attempts_per_test", "validation_reruns", "max_concurrent_open_prs"}
ATTEMPT_LIST_KEYS = {
    "id", "fixer_run_id", "test_fingerprint", "test_name", "status",
    "attempt_no", "patch_summary", "validation", "pr_url", "reason",
    "created_at", "completed_at",
}
ATTEMPT_DETAIL_EXTRA = {"patch", "runner_log_digest", "ledger_run_id"}


class _StageDB:
    """Minimal staging session (add/flush/execute-none) for upsert tests."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def execute(self, *_a, **_k):
        class _R:
            def scalar_one_or_none(self_inner):
                return None
        return _R()


# ── Config defaults + contract shape ─────────────────────────────────────────


def test_config_defaults_when_unconfigured():
    cfg = svc.serialize_fixer_config(None)
    assert set(cfg) == CONFIG_KEYS
    assert cfg["enabled"] is False
    assert cfg["mode"] == "shadow"
    assert set(cfg["runner"]) == RUNNER_KEYS
    assert cfg["runner"]["type"] == "none"
    assert cfg["test_globs"] == ["tests/**", "**/*.spec.*", "**/*.test.*"]
    assert set(cfg["budgets"]) == BUDGET_KEYS
    assert cfg["budgets"] == {
        "max_tests_per_run": 3, "max_attempts_per_test": 2,
        "validation_reruns": 5, "max_concurrent_open_prs": 2,
    }
    assert cfg["schedule"] == "off"


def test_config_roundtrip_from_policy_row():
    row = AgentPolicy(
        id=uuid.uuid4(), project_id=PROJECT_ID, agent_id="fixer",
        enabled=True, mode="suggest",
        budgets={
            "max_tests_per_run": 5, "max_attempts_per_test": 1,
            "validation_reruns": 7, "max_concurrent_open_prs": 4,
            "runner": {"type": "docker", "runner_image": "python:3.11", "command_template": "pytest {test_selector}", "workflow_ref": None},
            "test_globs": ["tests/**"], "schedule": "daily",
        },
    )
    cfg = svc.serialize_fixer_config(row)
    assert cfg["enabled"] is True and cfg["mode"] == "suggest"
    assert cfg["runner"]["type"] == "docker"
    assert cfg["runner"]["runner_image"] == "python:3.11"
    assert cfg["budgets"]["validation_reruns"] == 7
    assert cfg["schedule"] == "daily"


def test_validation_reruns_floored_at_one():
    row = AgentPolicy(
        id=uuid.uuid4(), project_id=PROJECT_ID, agent_id="fixer",
        enabled=True, mode="shadow", budgets={"validation_reruns": 0},
    )
    assert svc.serialize_fixer_config(row)["budgets"]["validation_reruns"] == 1


# ── Policy layer: act is rejected (reserved) ─────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_rejects_act_mode():
    db = _StageDB()
    with pytest.raises(ValueError):
        await svc.upsert_fixer_config(
            db, PROJECT_ID, enabled=True, mode="act",
            runner={"type": "docker"}, test_globs=["tests/**"],
            budgets={}, schedule="off",
        )


@pytest.mark.asyncio
async def test_upsert_stores_fixer_block_in_budgets():
    db = _StageDB()
    row = await svc.upsert_fixer_config(
        db, PROJECT_ID, enabled=True, mode="suggest",
        runner={"type": "workflow_dispatch", "workflow_ref": "flaky.yml"},
        test_globs=["tests/**", "e2e/**"], budgets={"max_tests_per_run": 9},
        schedule="weekly",
    )
    assert row.enabled is True and row.mode == "suggest"
    assert row.budgets["runner"]["type"] == "workflow_dispatch"
    assert row.budgets["runner"]["workflow_ref"] == "flaky.yml"
    assert row.budgets["test_globs"] == ["tests/**", "e2e/**"]
    assert row.budgets["schedule"] == "weekly"
    assert row.budgets["max_tests_per_run"] == 9
    # Round-trips back through the serializer to the pinned contract shape.
    assert set(svc.serialize_fixer_config(row)) == CONFIG_KEYS


# ── Attempt serialization key-sets ───────────────────────────────────────────


def _attempt(**over) -> FixAttempt:
    row = FixAttempt(
        id=uuid.uuid4(), project_id=PROJECT_ID, fixer_run_id=uuid.uuid4(),
        test_fingerprint="fp" * 8, test_name="tests/test_x.py::test_k",
        status="validated", attempt_no=1, patch_summary="1 file +1/-1",
        patch="--- a\n+++ b\n", validation_reruns=5, validation_passed=5,
        runner_log_digest="deadbeef", pr_url=None, pr_number=None, pr_state=None,
        ledger_run_id=uuid.uuid4(), reason="validated (shadow)",
        created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    )
    for k, v in over.items():
        setattr(row, k, v)
    return row


def test_attempt_list_shape():
    data = svc.serialize_attempt(_attempt())
    assert set(data) == ATTEMPT_LIST_KEYS
    assert data["validation"] == {"reruns": 5, "passed": 5}


def test_attempt_detail_shape():
    data = svc.serialize_attempt(_attempt(), detail=True)
    assert set(data) == ATTEMPT_LIST_KEYS | ATTEMPT_DETAIL_EXTRA
    assert data["patch"] is not None
    assert data["ledger_run_id"] is not None


def test_attempt_validation_null_when_never_validated():
    data = svc.serialize_attempt(_attempt(validation_reruns=None, validation_passed=None))
    assert data["validation"] is None


# ── Unified-diff applier (PR-side file reconstruction) ───────────────────────


def test_apply_unified_diff_roundtrip():
    base = "line1\nassert flaky\nline3\n"
    file_diff = (
        "--- a/t.py\n+++ b/t.py\n@@ -1,3 +1,3 @@\n line1\n-assert flaky\n+assert True\n line3\n"
    )
    out = apply_unified_diff(base, file_diff)
    assert out == "line1\nassert True\nline3\n"


def test_apply_unified_diff_context_mismatch_returns_none():
    base = "totally\ndifferent\n"
    file_diff = "@@ -1,2 +1,2 @@\n line1\n-assert flaky\n+assert True\n line3\n"
    assert apply_unified_diff(base, file_diff) is None


def test_split_per_file_diffs():
    patch = (
        "diff --git a/tests/a.py b/tests/a.py\n--- a/tests/a.py\n+++ b/tests/a.py\n@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/tests/b.py b/tests/b.py\n--- a/tests/b.py\n+++ b/tests/b.py\n@@ -1 +1 @@\n-p\n+q\n"
    )
    parts = split_per_file_diffs(patch)
    assert set(parts) == {"tests/a.py", "tests/b.py"}
