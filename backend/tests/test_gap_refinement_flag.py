"""AGT-16 (G-B runtime): the deep gap-detection / report-refinement stages follow
the per-project ``aiq_gap_refinement`` flag, frozen at pipeline start.

Before W04 both the planner and the two nodes read
``settings.AIQ_GAP_REFINEMENT_ENABLED`` (an env switch, default off), so every
deep decision report printed "Analysis coverage: not recorded". Now:

* ``_create_pipeline_run`` resolves the flag once (deep only, ``fresh=True``)
  and freezes it as ``gap_refinement_settings`` / ``state["gap_refinement_enabled"]``
  (live-Postgres coverage: ``tests/integration/test_ai_config_snapshot_postgres.py``);
* ``build_workflow_plan(gap_refinement_enabled=...)`` plans the two stages from
  it; ``plan["specialist_flags"]["gap_refinement"]`` exists on DEEP plans only,
  so every offline/live plan hash is byte-identical to the pre-W04 planner
  (goldens below were computed with the HEAD fd68b26d planner);
* the legacy env variable is only the fallback while no flag row exists.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.agent_planner import attach_workflow_plan_and_verification, build_workflow_plan
from app.services.feature_flags import LEGACY_ENV_VAR_MAP, is_enabled

GAP_STAGES = ("gap_detection", "report_refinement")
OFF = "AIQ gap/refinement feature flag is off for this project"
ON = "AIQ-P4 gap/refinement stage enabled by feature flag"

#: plan_sha256 of the pre-W04 planner (HEAD fd68b26d) for the same inputs.
#: Regenerate ONLY for a deliberate plan-shape change, never for this flag.
GOLDEN = {
    "offline-default": "0fae0f9f877639ff61ae6a066ed4d9ba2540437db9ebfa06a6fd71d18a36f15a",
    "offline-analyses": "f11d06b1bcc49c445f2c748d0fd4d3aab21d1fcd6a3bcf03a5c0c8fff6924d6d",
    "live-default": "7fa34a539d556f877107c9dbfa118e5c239981f706a02f56132a92e57f770ef6",
}


def _entry(plan: dict, stage: str) -> dict:
    return next(item for item in plan["stages"] if item["stage"] == stage)


# ── the planner ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", GAP_STAGES)
def test_a_deep_plan_follows_the_flag(stage):
    on = build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"], gap_refinement_enabled=True)
    off = build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"], gap_refinement_enabled=False)

    assert (_entry(on, stage)["planned"], _entry(on, stage)["rationale"]) == (True, ON)
    assert (_entry(off, stage)["planned"], _entry(off, stage)["rationale"]) == (False, OFF)
    assert on["specialist_flags"]["gap_refinement"] is True
    assert off["specialist_flags"]["gap_refinement"] is False
    assert on["plan_sha256"] != off["plan_sha256"]


def test_the_env_switch_no_longer_reaches_the_planner(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "AIQ_GAP_REFINEMENT_ENABLED", True)
    plan = build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"])
    assert _entry(plan, "gap_detection")["planned"] is False


def test_offline_and_live_plan_hashes_are_byte_identical_to_the_pre_w04_planner():
    assert build_workflow_plan(workflow_type="offline")["plan_sha256"] == GOLDEN["offline-default"]
    assert build_workflow_plan(
        workflow_type="offline", failed_test_ids=["t1", "t2"], analyses={"t1": {"confidence_score": 90}},
    )["plan_sha256"] == GOLDEN["offline-analyses"]
    assert build_workflow_plan(workflow_type="live")["plan_sha256"] == GOLDEN["live-default"]
    for workflow_type in ("offline", "live"):
        assert "gap_refinement" not in build_workflow_plan(workflow_type=workflow_type)["specialist_flags"]


@pytest.mark.parametrize("enabled", [True, False])
def test_the_rebuild_keeps_what_the_frozen_plan_decided(enabled):
    """Without threading the flag through ``attach_workflow_plan_and_verification``
    the rebuilt plan said "off" while the stages ran."""
    plan = build_workflow_plan(workflow_type="deep", failed_test_ids=["t1"], gap_refinement_enabled=enabled)
    state = attach_workflow_plan_and_verification(
        {"initial_workflow_plan": plan, "failed_test_ids": ["t1"], "analyses": {}},
        workflow_type="deep",
    )
    for stage in GAP_STAGES:
        assert _entry(state["workflow_plan"], stage)["planned"] is enabled
    # The explained plan carries real outcomes (so its hash differs from the
    # frozen one), but the flag it was rebuilt with is the frozen one.
    assert state["workflow_plan"]["specialist_flags"]["gap_refinement"] is enabled


# ── the flag and its legacy env fallback ────────────────────────────────────


class _Db:
    """The one query ``is_enabled(..., fresh=True)`` makes: the flag row or none."""

    def __init__(self, row=None) -> None:
        self.row = row

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)


def _row(**overrides):
    values = {
        "id": "00000000-0000-0000-0000-000000000209",
        "key": "aiq_gap_refinement",
        "description": "seeded",
        "enabled_global": True,
        "enabled_projects": None,
        "enabled_roles": None,
        "rollout_percent": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_the_legacy_env_variable_is_registered():
    assert LEGACY_ENV_VAR_MAP["aiq_gap_refinement"] == "AIQ_GAP_REFINEMENT_ENABLED"


@pytest.mark.asyncio
async def test_no_row_falls_back_to_the_legacy_env_variable(monkeypatch):
    monkeypatch.setenv("AIQ_GAP_REFINEMENT_ENABLED", "true")
    assert await is_enabled("aiq_gap_refinement", db=_Db(), fresh=True) is True
    monkeypatch.setenv("AIQ_GAP_REFINEMENT_ENABLED", "off")
    assert await is_enabled("aiq_gap_refinement", db=_Db(), fresh=True) is False
    monkeypatch.delenv("AIQ_GAP_REFINEMENT_ENABLED")
    assert await is_enabled("aiq_gap_refinement", db=_Db(), fresh=True) is False


@pytest.mark.asyncio
async def test_a_row_wins_over_the_env_variable(monkeypatch):
    """With the 0209 row present the env variable is ignored (documented)."""
    monkeypatch.setenv("AIQ_GAP_REFINEMENT_ENABLED", "false")
    assert await is_enabled("aiq_gap_refinement", db=_Db(_row()), fresh=True) is True
    monkeypatch.setenv("AIQ_GAP_REFINEMENT_ENABLED", "true")
    assert await is_enabled("aiq_gap_refinement", db=_Db(_row(enabled_global=False)), fresh=True) is False


@pytest.mark.asyncio
async def test_off_for_one_project_only():
    """Global ON narrowed by ``enabled_projects``: the listed project runs the
    stages, another project does not."""
    import uuid

    listed, other = uuid.uuid4(), uuid.uuid4()
    db = _Db(_row(enabled_projects=[str(listed)]))
    assert await is_enabled("aiq_gap_refinement", db=db, project_id=listed, fresh=True) is True
    assert await is_enabled("aiq_gap_refinement", db=db, project_id=other, fresh=True) is False


# ── nodes: frozen state, never the environment ──────────────────────────────


def test_the_nodes_no_longer_read_the_env_setting():
    """The setting stays in config.py as legacy-fallback documentation; no
    runtime module reads it any more."""
    import ast
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    readers = sorted(
        path.relative_to(app).as_posix()
        for path in app.rglob("*.py")
        if any(
            isinstance(node, ast.Attribute) and node.attr == "AIQ_GAP_REFINEMENT_ENABLED"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    )
    assert readers == [], readers


@pytest.mark.parametrize(
    "plan,expected",
    [
        ({"specialist_flags": {"gap_refinement": True}, "stages": []}, True),
        ({"specialist_flags": {"gap_refinement": False}, "stages": [{"stage": "gap_detection", "planned": True}]}, False),
        ({"stages": [{"stage": "gap_detection", "planned": True}]}, True),     # pre-W04 deep plan
        ({"stages": [{"stage": "gap_detection", "planned": False}]}, False),
        ({"stages": [{"stage": "ingestion"}]}, False),                         # offline plan
        (None, False),
    ],
)
def test_a_legacy_resume_takes_the_flag_from_what_its_plan_decided(plan, expected):
    from app.agents.workflow import _plan_gap_refinement_planned

    assert _plan_gap_refinement_planned(plan) is expected
