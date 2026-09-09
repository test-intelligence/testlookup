"""Regression guards for resumable, project-safe semantic reindexing."""

import uuid
import gc
import tracemalloc
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _rows(count: int):
    return [
        SimpleNamespace(
            id=uuid.UUID(int=index + 1),
            test_name=f"test-{index}",
            suite_name="suite",
            error_message=None,
            status="passed",
            test_run_id=f"run-{index}",
            project_id="project-a",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            step_text=None,
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_incremental_upsert_checkpoints_every_completed_batch(monkeypatch):
    from app.services import semantic_search

    rows = _rows(401)
    collection = MagicMock()
    checkpoints = MagicMock()
    monkeypatch.setattr(semantic_search, "_update_cursor", checkpoints)
    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", 200)

    count = await semantic_search._upsert_rows_to_collection(
        collection,
        rows,
        checkpoint_cursor=True,
        cursor_project_id="project-a",
    )

    assert count == 401
    assert [call.args[0][-1].id for call in checkpoints.call_args_list] == [
        uuid.UUID(int=200),
        uuid.UUID(int=400),
        uuid.UUID(int=401),
    ]
    assert all(call.args[1] == "project-a" for call in checkpoints.call_args_list)


@pytest.mark.asyncio
async def test_failed_later_batch_preserves_prior_checkpoint(monkeypatch):
    from app.services import semantic_search

    rows = _rows(401)
    collection = MagicMock()
    collection.upsert.side_effect = [None, RuntimeError("embedding timeout")]
    checkpoints = MagicMock()
    monkeypatch.setattr(semantic_search, "_update_cursor", checkpoints)
    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", 200)

    with pytest.raises(RuntimeError, match="embedding timeout"):
        await semantic_search._upsert_rows_to_collection(
            collection,
            rows,
            checkpoint_cursor=True,
            cursor_project_id="project-a",
        )

    checkpoints.assert_called_once()
    assert checkpoints.call_args.args[0][-1].id == uuid.UUID(int=200)


def test_project_cursor_keys_cannot_advance_the_global_cursor():
    from app.services.semantic_search import (
        _REDIS_CURSOR_KEY,
        _REDIS_TIMESTAMP_KEY,
        _cursor_keys,
    )

    assert _cursor_keys() == (_REDIS_CURSOR_KEY, _REDIS_TIMESTAMP_KEY)
    project_keys = _cursor_keys("project-a")
    assert project_keys[0] != _REDIS_CURSOR_KEY
    assert project_keys[1] != _REDIS_TIMESTAMP_KEY
    assert project_keys[0].endswith(":project:project-a")


def test_cursor_predicate_keeps_equal_timestamp_rows_after_the_exact_id():
    from app.services.semantic_search import _after_incremental_cursor

    sql = str(_after_incremental_cursor(uuid.uuid4()))

    assert "test_cases.created_at >" in sql
    assert "test_cases.created_at =" in sql
    assert "test_cases.id >" in sql


def test_missing_cursor_row_restarts_instead_of_filtering_everything():
    from app.services.semantic_search import _after_incremental_cursor

    sql = str(_after_incremental_cursor(uuid.uuid4())).lower()

    assert "coalesce" in sql


@pytest.mark.asyncio
async def test_full_reindex_uses_bounded_pages_and_checkpoints_each_success(monkeypatch):
    from app.services import semantic_search

    rows = _rows(5)
    statements = []

    class _Result:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class _DB:
        async def execute(self, statement):
            statements.append(statement)
            page = len(statements) - 1
            values = rows[page * 2:(page + 1) * 2]
            return _Result(values)

    monkeypatch.setattr(
        semantic_search,
        "_get_or_create_collection",
        AsyncMock(return_value=MagicMock()),
    )
    state = {
        "scope_key": "global",
        "job_id": uuid.uuid4(),
        "status": "running",
        "high_water_created_at": rows[-1].created_at,
        "high_water_id": rows[-1].id,
        "cursor_created_at": None,
        "cursor_id": None,
        "processed_count": 0,
        "lease_owner": uuid.uuid4(),
        "fence_token": 1,
    }
    monkeypatch.setattr(
        semantic_search, "_claim_full_reindex_job", AsyncMock(return_value=state)
    )
    monkeypatch.setattr(
        semantic_search, "_renew_full_reindex_lease", AsyncMock()
    )
    upsert = AsyncMock(side_effect=lambda _collection, page: len(page))
    monkeypatch.setattr(semantic_search, "_upsert_rows_to_collection", upsert)
    checkpoints = AsyncMock()
    monkeypatch.setattr(
        semantic_search, "_checkpoint_full_reindex_page", checkpoints
    )
    mark_finalizing = AsyncMock(side_effect=lambda _db, current: current.update(status="finalizing"))
    monkeypatch.setattr(
        semantic_search, "_mark_full_reindex_finalizing", mark_finalizing
    )
    completed = AsyncMock()
    monkeypatch.setattr(semantic_search, "_complete_full_reindex_job", completed)
    monkeypatch.setattr(
        semantic_search, "_update_cursor_from_values", MagicMock(return_value=True)
    )
    monkeypatch.setattr(semantic_search, "_publish_index_size", AsyncMock())
    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", 2)

    assert await semantic_search.index_test_cases(_DB()) == 5

    assert [len(call.args[1]) for call in upsert.call_args_list] == [2, 2, 1]
    assert checkpoints.call_count == 3
    assert [call.args[3] for call in checkpoints.call_args_list] == [2, 4, 5]
    mark_finalizing.assert_awaited_once()
    completed.assert_awaited_once()
    assert all("LIMIT" in str(statement) for statement in statements)
    assert all(
        "ORDER BY test_cases.created_at ASC, test_cases.id ASC" in str(statement)
        for statement in statements
    )


@pytest.mark.asyncio
async def test_full_reindex_failure_resumes_after_last_successful_page(monkeypatch):
    from app.services import semantic_search

    rows = _rows(4)
    initial = {
        "scope_key": "global",
        "job_id": uuid.uuid4(),
        "status": "running",
        "high_water_created_at": rows[-1].created_at,
        "high_water_id": rows[-1].id,
        "cursor_created_at": None,
        "cursor_id": None,
        "processed_count": 0,
        "lease_owner": uuid.uuid4(),
        "fence_token": 1,
    }

    class _Result:
        def __init__(self, values):
            self.values = values
        def all(self):
            return self.values

    class _DB:
        calls = 0
        async def execute(self, _statement):
            self.calls += 1
            return _Result(rows[:2] if self.calls == 1 else rows[2:4])

    monkeypatch.setattr(semantic_search, "_get_or_create_collection", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(semantic_search, "_claim_full_reindex_job", AsyncMock(return_value=initial))
    monkeypatch.setattr(semantic_search, "_renew_full_reindex_lease", AsyncMock())
    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", 2)
    monkeypatch.setattr(
        semantic_search,
        "_upsert_rows_to_collection",
        AsyncMock(side_effect=[2, RuntimeError("worker interrupted")]),
    )
    checkpoint = AsyncMock()
    monkeypatch.setattr(semantic_search, "_checkpoint_full_reindex_page", checkpoint)
    release = AsyncMock()
    monkeypatch.setattr(semantic_search, "_release_full_reindex_after_error", release)

    assert await semantic_search.index_test_cases(_DB()) is None
    checkpoint.assert_awaited_once()
    assert checkpoint.call_args.args[1:] == (initial, rows[1], 2)
    release.assert_awaited_once()


def test_full_reindex_state_is_scoped():
    from app.services import semantic_search

    assert semantic_search._full_reindex_scope(None) == "global"
    assert semantic_search._full_reindex_scope("project-a") == "project:project-a"


@pytest.mark.asyncio
async def test_finalizing_resume_does_not_replay_a_page(monkeypatch):
    from app.services import semantic_search

    state = {
        "scope_key": "global",
        "job_id": uuid.uuid4(),
        "status": "finalizing",
        "high_water_created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "high_water_id": uuid.uuid4(),
        "cursor_created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "cursor_id": uuid.uuid4(),
        "processed_count": 7,
        "lease_owner": uuid.uuid4(),
        "fence_token": 4,
    }
    db = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(semantic_search, "_get_or_create_collection", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(semantic_search, "_claim_full_reindex_job", AsyncMock(return_value=state))
    monkeypatch.setattr(semantic_search, "_update_cursor_from_values", MagicMock(return_value=True))
    complete = AsyncMock()
    monkeypatch.setattr(semantic_search, "_complete_full_reindex_job", complete)
    monkeypatch.setattr(semantic_search, "_publish_index_size", AsyncMock())

    assert await semantic_search.index_test_cases(db) == 7
    db.execute.assert_not_awaited()
    complete.assert_awaited_once_with(db, state)


@pytest.mark.asyncio
async def test_fenced_checkpoint_rejects_a_stale_owner():
    from app.services import semantic_search

    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)),
        rollback=AsyncMock(),
        commit=AsyncMock(),
    )
    state = {
        "scope_key": "global",
        "job_id": uuid.uuid4(),
        "lease_owner": uuid.uuid4(),
        "fence_token": 2,
    }

    with pytest.raises(semantic_search._FullReindexOwnershipLost):
        await semantic_search._guarded_job_update(
            db, state, expected_status="running", values={"processed_count": 3}
        )

    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_reindex_resumes_same_snapshot_after_interruption(monkeypatch):
    from app.services import semantic_search

    rows = _rows(5)
    state = {
        "scope_key": "global",
        "job_id": uuid.uuid4(),
        "status": "running",
        "high_water_created_at": rows[-1].created_at,
        "high_water_id": rows[-1].id,
        "cursor_created_at": None,
        "cursor_id": None,
        "processed_count": 0,
        "lease_owner": uuid.uuid4(),
        "fence_token": 1,
    }

    class _Result:
        def __init__(self, values):
            self.values = values
        def all(self):
            return self.values

    class _DB:
        async def execute(self, _statement):
            start = 0
            if state["cursor_id"] is not None:
                start = next(i for i, row in enumerate(rows) if row.id == state["cursor_id"]) + 1
            return _Result(rows[start:start + 2])

    landed = []
    calls = 0

    async def upsert(_collection, page):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("worker interrupted")
        landed.extend(row.id for row in page)
        return len(page)

    async def checkpoint(_db, current, last_row, count):
        current.update(
            cursor_created_at=last_row.created_at,
            cursor_id=last_row.id,
            processed_count=count,
        )

    async def finalizing(_db, current):
        current["status"] = "finalizing"

    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", 2)
    monkeypatch.setattr(semantic_search, "_get_or_create_collection", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(semantic_search, "_claim_full_reindex_job", AsyncMock(return_value=state))
    monkeypatch.setattr(semantic_search, "_renew_full_reindex_lease", AsyncMock())
    monkeypatch.setattr(semantic_search, "_upsert_rows_to_collection", upsert)
    monkeypatch.setattr(semantic_search, "_checkpoint_full_reindex_page", checkpoint)
    monkeypatch.setattr(semantic_search, "_release_full_reindex_after_error", AsyncMock())
    monkeypatch.setattr(semantic_search, "_mark_full_reindex_finalizing", finalizing)
    monkeypatch.setattr(semantic_search, "_complete_full_reindex_job", AsyncMock())
    monkeypatch.setattr(semantic_search, "_update_cursor_from_values", MagicMock(return_value=True))
    monkeypatch.setattr(semantic_search, "_publish_index_size", AsyncMock())

    assert await semantic_search.index_test_cases(_DB()) is None
    assert state["cursor_id"] == rows[1].id
    assert state["high_water_id"] == rows[-1].id
    assert await semantic_search.index_test_cases(_DB()) == 5
    assert landed == [row.id for row in rows]


def test_keyset_snapshot_excludes_rows_above_high_watermark():
    from sqlalchemy.dialects import postgresql
    from app.services import semantic_search

    boundary_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    boundary_id = uuid.UUID(int=10)
    predicate = semantic_search._at_or_before_keyset(boundary_time, boundary_id)
    sql = str(
        predicate.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "test_cases.created_at <=" not in sql
    assert "test_cases.created_at <" in sql
    assert "test_cases.created_at =" in sql
    assert "test_cases.id <=" in sql


@pytest.mark.asyncio
async def test_full_reindex_peak_memory_is_constant_for_ten_times_corpus(monkeypatch):
    from app.services import semantic_search

    batch_size = 100
    monkeypatch.setattr(semantic_search.settings, "SEARCH_INDEX_BATCH_SIZE", batch_size)
    monkeypatch.setattr(semantic_search, "_get_or_create_collection", AsyncMock(return_value=SimpleNamespace(upsert=lambda **_: None, count=lambda: 0)))
    monkeypatch.setattr(semantic_search, "_renew_full_reindex_lease", AsyncMock())
    monkeypatch.setattr(semantic_search, "_checkpoint_full_reindex_page", AsyncMock())
    monkeypatch.setattr(semantic_search, "_mark_full_reindex_finalizing", AsyncMock(side_effect=lambda _db, state: state.update(status="finalizing")))
    monkeypatch.setattr(semantic_search, "_complete_full_reindex_job", AsyncMock())
    monkeypatch.setattr(semantic_search, "_update_cursor_from_values", MagicMock(return_value=True))
    monkeypatch.setattr(semantic_search, "_publish_index_size", AsyncMock())

    class _Result:
        def __init__(self, values): self.values = values
        def all(self): return self.values

    class _PagedDB:
        def __init__(self, total):
            self.total, self.offset, self.max_page = total, 0, 0
        async def execute(self, _statement):
            count = min(batch_size, self.total - self.offset)
            page = _rows(count)
            for index, row in enumerate(page):
                row.id = uuid.UUID(int=self.offset + index + 1)
            self.offset += count
            self.max_page = max(self.max_page, count)
            return _Result(page)

    async def peak(total):
        state = {
            "scope_key": "global", "job_id": uuid.uuid4(), "status": "running",
            "high_water_created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "high_water_id": uuid.UUID(int=total), "cursor_created_at": None,
            "cursor_id": None, "processed_count": 0, "lease_owner": uuid.uuid4(),
            "fence_token": 1,
        }
        monkeypatch.setattr(semantic_search, "_claim_full_reindex_job", AsyncMock(return_value=state))
        db = _PagedDB(total)
        gc.collect()
        tracemalloc.start()
        assert await semantic_search.index_test_cases(db) == total
        _, measured_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert db.max_page == batch_size
        return measured_peak

    small = await peak(1_000)
    large = await peak(10_000)
    assert large < small * 3
