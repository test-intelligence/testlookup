from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_persist_memory_entry_sanitizes_payload_and_sets_lifecycle_defaults():
    from app.services.agent_memory_service import persist_memory_entries

    db = AsyncMock()
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    with patch("app.services.agent_memory_service._index_memory_vector", new_callable=AsyncMock):
        count = await persist_memory_entries(
            db,
            [{
                "project_id": project_id,
                "run_id": run_id,
                "entity_type": "cluster",
                "entity_id": "cluster-1",
                "error_signature": "password=hunter2",
                "payload": {"authorization": "Bearer secret-value", "label": "safe"},
            }],
        )

    assert count == 1
    entry = db.add.call_args.args[0]
    assert entry.lifecycle_status == "active"
    assert entry.source_type == "pipeline_agent"
    assert entry.trust_level == "derived"
    assert entry.expires_at > datetime.now(timezone.utc)
    assert "hunter2" not in entry.error_signature
    assert "secret-value" not in str(entry.payload)


@pytest.mark.asyncio
async def test_invalid_memory_provenance_fails_closed_before_insert():
    from app.services.agent_memory_service import persist_memory_entries

    db = AsyncMock()
    with pytest.raises(ValueError, match="memory_provenance_invalid"):
        await persist_memory_entries(
            db,
            [{
                "project_id": uuid.uuid4(),
                "run_id": uuid.uuid4(),
                "entity_type": "cluster",
                "entity_id": "cluster-1",
                "source_type": "llm_guess",
            }],
        )
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_supersession_requires_same_tenant_active_rows():
    from app.services.agent_memory_service import supersede_memory_entry

    current_id = uuid.uuid4()
    replacement_id = uuid.uuid4()
    project_id = uuid.uuid4()
    current = MagicMock(id=current_id, lifecycle_status="active")
    replacement = MagicMock(id=replacement_id, lifecycle_status="active")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [current, replacement]
    db = AsyncMock()
    db.execute.return_value = result

    assert await supersede_memory_entry(
        db,
        project_id=project_id,
        memory_entry_id=current_id,
        replacement_entry_id=replacement_id,
    ) is True
    assert current.lifecycle_status == "superseded"
    assert current.superseded_by_id == replacement_id
    assert current.superseded_at is not None


def test_active_memory_filter_requires_fresh_lifecycle():
    from app.services.agent_memory_service import _active_memory_filters

    expressions = _active_memory_filters(datetime.now(timezone.utc))
    sql = " ".join(str(item) for item in expressions)
    assert "lifecycle_status" in sql
    assert "expires_at" in sql


@pytest.mark.asyncio
async def test_expire_rows_and_purge_matching_project_vectors():
    from app.services.agent_memory_service import expire_memory_entries, purge_memory_vectors

    db = AsyncMock()
    db.execute.return_value = MagicMock(rowcount=2)
    project_id = uuid.uuid4()
    assert await expire_memory_entries(db, project_id=project_id) == 2

    target_id = uuid.uuid4()
    collection = MagicMock()
    collection.get.return_value = {
        "ids": ["vec-a", "vec-b", "vec-c"],
        "metadatas": [
            {"entry_id": str(target_id), "project_id": str(project_id)},
            {"entry_id": "entry-b", "project_id": str(project_id)},
            {"entry_id": str(target_id), "project_id": str(project_id)},
        ],
    }
    with patch(
        "app.services.agent_memory_service._get_or_create_collection",
        new_callable=AsyncMock,
        return_value=collection,
    ):
        deleted = await purge_memory_vectors(project_id, [target_id])

    assert deleted == 2
    collection.delete.assert_called_once_with(ids=["vec-a", "vec-c"])
