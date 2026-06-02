"""Regression: ingestion inserted a duplicate TestCaseHistory row per Allure
retry (and per same-build re-ingest), corrupting flaky/historical metrics.

Bug pinned (review/ingestion-service, 2026-06-01):

Allure writes one ``-result.json`` per test *execution* (retries included), all
sharing the same fingerprint. ``process_sentinel`` doesn't dedup within Allure,
so ``_upsert_test_case`` is called twice for the same (run, fingerprint): the
first INSERTs the TestCase + a history row, the retry UPDATEs the TestCase but
**also unconditionally inserted a second TestCaseHistory row**. There is no
unique key on (test_case_id, test_run_id), so duplicates persisted and inflated
the per-fingerprint COUNT(*) that flaky detection + historical scoring divide
by. Fix: history is now idempotent per (test_case_id, test_run_id) — the update
path refreshes the existing row instead of inserting a second.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.models.postgres import TestCaseHistory as _TestCaseHistory  # noqa: E402  (aliased so pytest doesn't collect it)
from app.services import ingestion as svc  # noqa: E402


class _FakeDB:
    def __init__(self):
        self.added: list = []
        self._history_rows: list = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, _TestCaseHistory):
            self._history_rows.append(obj)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = uuid.uuid4()

    async def execute(self, stmt):
        sql = str(stmt)
        res = MagicMock()
        if "test_case_history" in sql:
            res.scalar_one_or_none = MagicMock(
                return_value=self._history_rows[0] if self._history_rows else None
            )
        else:  # the test_cases existence SELECT
            res.scalar_one_or_none = MagicMock(return_value=None)
        return res


@pytest.mark.asyncio
async def test_retry_upsert_does_not_duplicate_history():
    run = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDB()

    with patch(
        "app.services.privacy_service.sanitize_for_persistence",
        side_effect=lambda s: s,
    ):
        # First Allure result for the test → new TestCase + 1 history row.
        tc = await svc._upsert_test_case(
            db,
            {"test_name": "test_login", "class_name": "AuthSuite",
             "status": "failed", "duration_ms": 100},
            run,
            existing=None,
            fingerprint="fp1",
        )
        # Retry's extra -result.json for the SAME fingerprint → update path.
        await svc._upsert_test_case(
            db,
            {"test_name": "test_login", "class_name": "AuthSuite",
             "status": "passed", "duration_ms": 120},
            run,
            existing=tc,
            fingerprint="fp1",
        )

    history_rows = [o for o in db.added if isinstance(o, _TestCaseHistory)]
    assert len(history_rows) == 1, (
        f"expected exactly one history row per (case, run); got {len(history_rows)}"
    )
    # The single row reflects the retry's final status, not the first attempt.
    assert str(getattr(history_rows[0].status, "value", history_rows[0].status)) in (
        "PASSED", "passed",
    )
    assert history_rows[0].duration_ms == 120
