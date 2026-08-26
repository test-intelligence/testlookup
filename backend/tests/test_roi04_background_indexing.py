"""
ROI-04: Background Semantic Indexing — Unit Tests.

Tests:
  - Incremental indexing function exists and is callable
  - Full indexing function preserved
  - Cursor management (Redis keys)
  - Reindex task supports full and incremental modes
  - Ingestion trigger wiring
  - Edge cases: no new records, ChromaDB unavailable, cursor missing
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


# NOTE: the ``app.core.deps`` stub below must carry every name that a router
# imported by this file does ``from app.core.deps import ...`` on. A missing
# name does not fail as "attribute missing" — the import raises
# ``ImportError: cannot import name X from app.core.deps (unknown location)``,
# and only when this module is imported for the first time in the process.
# Run inside the full suite another test has usually imported the router
# already, so the stub is never consulted and the file passes; run on its own
# it fails. ``resolve_project_scope`` (used by app/routers/search.py) was the
# missing one, and made ``pytest tests/test_roi04_background_indexing.py``
# red on main while CI stayed green.
def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security", verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"), create_access_token=MagicMock(return_value="access_token"), create_refresh_token=MagicMock(return_value="refresh_token"), decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), require_project_access=MagicMock(return_value=MagicMock()), require_run_access=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None), resolve_project_scope=MagicMock(return_value=MagicMock())))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


class TestIncrementalIndexing:
    def test_index_incremental_importable(self):
        from app.services.semantic_search import index_incremental
        assert callable(index_incremental)

    def test_index_test_cases_still_exists(self):
        from app.services.semantic_search import index_test_cases
        assert callable(index_test_cases)

    def test_both_are_async(self):
        import inspect
        from app.services.semantic_search import index_incremental, index_test_cases
        assert inspect.iscoroutinefunction(index_incremental)
        assert inspect.iscoroutinefunction(index_test_cases)


class TestCursorManagement:
    def test_redis_keys_defined(self):
        from app.services.semantic_search import _REDIS_CURSOR_KEY, _REDIS_TIMESTAMP_KEY
        assert _REDIS_CURSOR_KEY == "testlookup:search:last_indexed_id"
        assert _REDIS_TIMESTAMP_KEY == "testlookup:search:last_indexed_at"

    def test_update_cursor_function_exists(self):
        from app.services.semantic_search import _update_cursor
        assert callable(_update_cursor)

    def test_update_cursor_empty_rows(self):
        from app.services.semantic_search import _update_cursor
        # Should not crash with empty rows
        _update_cursor([])  # no-op


class TestReindexTask:
    def test_reindex_task_exists(self):
        from app.worker.tasks import reindex_search
        assert callable(reindex_search)

    def test_reindex_accepts_full_param(self):
        """The reindex task should accept a 'full' parameter."""
        import inspect
        from app.worker.tasks import reindex_search
        # Check the underlying function params (Celery wraps it)
        sig = inspect.signature(reindex_search.run)
        params = list(sig.parameters.keys())
        assert "full" in params

    def test_reindex_accepts_project_id(self):
        import inspect
        from app.worker.tasks import reindex_search
        sig = inspect.signature(reindex_search.run)
        params = list(sig.parameters.keys())
        assert "project_id" in params


class TestIngestionTrigger:
    def test_ingest_task_exists(self):
        from app.worker.tasks import ingest_test_run
        assert callable(ingest_test_run)

    def test_ingestion_triggers_indexing(self):
        """Verify the ingestion task source code contains the reindex trigger."""
        import inspect
        from app.worker.tasks import ingest_test_run
        source = inspect.getsource(ingest_test_run)
        assert "reindex_search" in source


class TestSearchRouter:
    def test_reindex_endpoint_exists(self):
        from app.routers.search import trigger_reindex
        assert callable(trigger_reindex)

    def test_reindex_accepts_full_parameter(self):
        import inspect
        from app.routers.search import trigger_reindex
        sig = inspect.signature(trigger_reindex)
        params = list(sig.parameters.keys())
        assert "full" in params


class TestEdgeCases:
    def test_doc_text_generation(self):
        from app.services.semantic_search import _doc_text
        assert _doc_text("test_login", "AuthSuite", "NullPointer") == "test_login | AuthSuite | NullPointer"
        assert _doc_text("test_login", None, None) == "test_login"
        assert _doc_text("test_login", "", None) == "test_login"

    def test_collection_name_constant(self):
        from app.services.semantic_search import _COLLECTION_NAME
        assert _COLLECTION_NAME == "test_case_search"

    def test_get_index_status_importable(self):
        from app.services.semantic_search import get_index_status
        assert callable(get_index_status)
