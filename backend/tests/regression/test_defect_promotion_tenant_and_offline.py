"""Regression: defect-promotion dedup leaked across tenants, and Jira was
posted in offline mode.

Bugs pinned (review/defect-promotion-service, 2026-06-01):

1. ``_find_duplicate_semantic`` upserted/queried a single global ChromaDB
   collection ``"open_defects"`` (no project namespace, no metadata filter).
   The collection persists across calls and accumulates every project's
   defects, so a nearest-neighbour query could return ANOTHER tenant's defect
   — linking a foreign duplicate_of FK and suppressing a legitimate Jira
   ticket. Fix: per-project collection ``f"open_defects_{project_id}"``.

2. ``promote_cluster`` created a Jira ticket without checking
   ``AI_OFFLINE_MODE`` (``jira_client`` checks only ``JIRA_ENABLED``). The
   offline kill-switch must block every outbound integration. Fix: gate the
   Jira block on ``not settings.AI_OFFLINE_MODE``.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import defect_promotion_service as svc  # noqa: E402


class _ScalarsResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def scalar_one_or_none(self):
        return self._scalar


@pytest.mark.asyncio
async def test_chroma_dedup_collection_is_per_project():
    project_id = str(uuid.uuid4())
    open_defect = SimpleNamespace(id=uuid.uuid4(), title="Checkout 500", cluster_id="c1")

    db = AsyncMock()
    # Single PG query in this path: load open defects for the project.
    db.execute = AsyncMock(return_value=_ScalarsResult(rows=[open_defect]))

    captured = {}

    class _FakeColl:
        def upsert(self, ids=None, documents=None):
            captured["upsert_ids"] = ids

        def query(self, query_texts=None, n_results=1):
            # No match → forces fall-through (and proves the name was captured
            # at get_or_create_collection time regardless).
            return {"ids": [[]], "distances": [[]]}

    class _FakeClient:
        def get_or_create_collection(self, name):
            captured["collection"] = name
            return _FakeColl()

    # _find_duplicate_semantic builds its client via app.db.chroma.get_chroma_client
    # (shared factory that disables ChromaDB telemetry). Patch that seam — the
    # legacy ``sys.modules['chromadb']`` patch no longer intercepts the call,
    # because app.db.chroma binds ``chromadb`` at its own import time.
    # Memory dedup must fall through (no entries) so the Chroma path runs.
    with patch(
        "app.services.agent_memory_service.find_duplicate_defect_memory",
        AsyncMock(return_value={}),
    ), patch("app.db.chroma.get_chroma_client", return_value=_FakeClient()):
        dup_id, found = await svc._find_duplicate_semantic(
            project_id=project_id, duplicate_hint="Checkout 500 error", db=db,
        )

    assert captured.get("collection") == f"open_defects_{project_id}", (
        "ChromaDB dedup collection must be namespaced per project to avoid "
        "cross-tenant duplicate matches"
    )


def _cluster():
    return SimpleNamespace(
        member_test_ids=[], label="Cluster L", size=1, representative_error="",
    )


async def _drive_promote(*, offline: bool, jira_spy: AsyncMock):
    db = AsyncMock()
    # member_test_ids=[] → analyses + tc_id queries are skipped; execute is
    # called for cluster, the run's release, then the finding.
    db.execute = AsyncMock(side_effect=[
        _ScalarsResult(scalar=_cluster()),   # cluster load
        _ScalarsResult(scalar=None),         # run release load
        _ScalarsResult(scalar=None),         # deep finding load
    ])
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch.object(svc, "_resolve_defect_owner_from_memory", AsyncMock(return_value=None)), \
         patch.object(svc, "_find_duplicate_semantic", AsyncMock(return_value=(None, False))), \
         patch.object(svc, "check_defect_promotion_policy", AsyncMock(return_value={
             # Production passes initial_status through str(...) and compares to
             # ActionStatus.APPROVED, so use the str-enum VALUE (matches the
             # real policy return) — not the enum member.
             "initial_status": svc.ActionStatus.APPROVED.value,
             "requires_approval": False,
             "policy_reasons": [],
         })), \
         patch.object(svc, "_create_jira_ticket", jira_spy), \
         patch.object(svc.settings, "AI_OFFLINE_MODE", offline):
        return await svc.promote_cluster(
            run_id=str(uuid.uuid4()),
            cluster_id="c1",
            project_id=str(uuid.uuid4()),
            request={"title": "T", "description": "d", "severity": "HIGH",
                     "project_key": "PROJ"},
            db=db,
        )


@pytest.mark.asyncio
async def test_promote_carries_the_source_runs_release_to_the_defect():
    """A promoted blocker must remain visible to its release gate."""
    release_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _ScalarsResult(scalar=_cluster()),
        _ScalarsResult(scalar=release_id),
        _ScalarsResult(scalar=None),
    ])
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch.object(svc, "_resolve_defect_owner_from_memory", AsyncMock(return_value=None)), \
         patch.object(svc, "_find_duplicate_semantic", AsyncMock(return_value=(None, False))), \
         patch.object(svc, "check_defect_promotion_policy", AsyncMock(return_value={
             "initial_status": svc.ActionStatus.APPROVED.value,
             "requires_approval": False,
             "policy_reasons": [],
         })):
        await svc.promote_cluster(
            run_id=str(uuid.uuid4()),
            cluster_id="c1",
            project_id=str(uuid.uuid4()),
            request={"title": "Release blocker", "severity": "HIGH"},
            db=db,
        )

    promoted = db.add.call_args.args[0]
    assert promoted.release_id == release_id


@pytest.mark.asyncio
async def test_promote_skips_jira_in_offline_mode():
    jira_spy = AsyncMock(return_value=({"key": "X-1"}, "url"))
    result = await _drive_promote(offline=True, jira_spy=jira_spy)
    jira_spy.assert_not_called()        # offline kill-switch blocked the POST
    assert result["jira_ticket"] is None


@pytest.mark.asyncio
async def test_promote_creates_jira_when_online():
    """Meta-test: the same eligible promotion DOES create the ticket online,
    proving the offline guard (not a mismatched mock) is what suppressed it."""
    jira_spy = AsyncMock(return_value=({"key": "X-1"}, "https://jira/X-1"))
    result = await _drive_promote(offline=False, jira_spy=jira_spy)
    jira_spy.assert_awaited_once()
    assert result["jira_ticket"] == {"key": "X-1"}
