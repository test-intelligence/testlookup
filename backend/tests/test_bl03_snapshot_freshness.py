"""BL-03: snapshot invalidation and freshness.

Rewritten 2026-09-20. Every assertion in this file was previously vacuous --
literals compared to themselves::

    def test_release_override_should_mark_stale(self):
        action = "mark_stale"
        assert action == "mark_stale"

    def test_non_blocking_invalidation(self):
        errors_swallowed = True
        assert errors_swallowed

Fifteen of the twenty-one tests were of that form. The file was the named guard
for the freshness contract and could not fail for any change to it, which is why
two real defects lived here undisturbed: ``get_stale_snapshot`` served a row
whose refresh was never scheduled, so a committed ``GO -> NO_GO`` override left
``/intelligence`` answering ``GO`` indefinitely; and both export paths read the
table with neither predicate.

The old file also asserted the WRONG contract: a release override does not mark
the snapshot stale, it deletes it (see ``TestEachTriggerUsesTheRightPrimitive``).
A tautology cannot notice that it is describing behaviour that no longer exists.

**What can and cannot be tested here.** ``get_cached_snapshot`` applies its
``stale`` filter in SQL and its ``schema_version`` check in Python. A fake
session ignores a WHERE clause entirely, so the SQL half is asserted against the
compiled statement and only the Python half behaviourally. Asserting the SQL
half through a mock would be another test that cannot fail.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import intelligence_snapshot_service as svc

RUN = uuid.uuid4()


def _row(*, schema_version: int, stale: bool = False, payload=None):
    """A snapshot row as the ORM returns it."""
    return SimpleNamespace(
        payload=payload if payload is not None else {"run": {"id": str(RUN)}},
        schema_version=schema_version,
        stale=stale,
    )


class _Db:
    """Returns one prepared row for any statement, recording what it was asked."""

    def __init__(self, row=None, rowcount: int = 0):
        self._row = row
        self._rowcount = rowcount
        self.statements: list[object] = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)
        row = self._row
        rowcount = self._rowcount
        return SimpleNamespace(
            scalar_one_or_none=lambda: row,
            rowcount=rowcount,
        )

    async def commit(self):
        self.committed = True


def _sql(statement) -> str:
    from sqlalchemy.dialects import postgresql

    return " ".join(str(statement.compile(dialect=postgresql.dialect())).split())


class TestFreshnessIsDecidedOnTwoAxes:
    """``stale`` and ``schema_version`` are different problems.

    A stale row has the right shape and out-of-date content -- servable while a
    refresh runs. A row at a superseded ``schema_version`` has the WRONG SHAPE,
    and serving it hands the consumer a payload the current contract says cannot
    exist.
    """

    @pytest.mark.asyncio
    async def test_a_current_fresh_row_is_served(self):
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION))
        assert await svc.get_cached_snapshot(db, RUN) == {"run": {"id": str(RUN)}}

    @pytest.mark.asyncio
    async def test_an_obsolete_row_is_refused_by_the_fresh_read(self):
        # Python-side check, so a fake session can prove it.
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION - 1))
        assert await svc.get_cached_snapshot(db, RUN) is None

    @pytest.mark.asyncio
    async def test_a_newer_schema_version_is_still_served(self):
        # The comparison is >=, not ==: a row written by a newer deploy during
        # a rollout is shape-compatible, and refusing it would recompute every
        # read until the rollout finished.
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION + 1))
        assert await svc.get_cached_snapshot(db, RUN) is not None

    @pytest.mark.asyncio
    async def test_the_fresh_read_filters_stale_in_sql(self):
        # Asserted on the statement, not through the fake: a mocked execute
        # ignores WHERE, so a behavioural test here could not fail.
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION))
        await svc.get_cached_snapshot(db, RUN)
        sql = _sql(db.statements[0]).lower()
        assert "stale is false" in sql or "stale = false" in sql, sql

    @pytest.mark.asyncio
    async def test_the_stale_read_filters_the_schema_version_in_sql(self):
        """The bug this predicate exists for.

        ``get_stale_snapshot`` had no version filter, so it defeated the bump on
        its sibling: the fresh read correctly refused an obsolete row, the
        request fell through, and the same row came back with ``stale: true``.
        """
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION, stale=True))
        await svc.get_stale_snapshot(db, RUN)
        sql = _sql(db.statements[0]).lower()
        assert "schema_version >=" in sql, sql

    @pytest.mark.asyncio
    async def test_the_stale_read_does_not_filter_on_stale(self):
        # Serving a stale row is its whole purpose; filtering it out would make
        # the function unreachable.
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION, stale=True))
        await svc.get_stale_snapshot(db, RUN)
        assert "stale is false" not in _sql(db.statements[0]).lower()

    @pytest.mark.asyncio
    async def test_a_missing_row_is_none_from_both_reads(self):
        assert await svc.get_cached_snapshot(_Db(None), RUN) is None
        assert await svc.get_stale_snapshot(_Db(None), RUN) is None


class TestEachTriggerUsesTheRightPrimitive:
    """Three primitives, three different guarantees. The old file asserted a
    string literal for this and therefore could not notice when one changed."""

    def test_pipeline_completion_deletes(self):
        # A completed pipeline replaces the analysis wholesale; the old payload
        # is not merely out of date, it describes a superseded run.
        import inspect

        from app.worker import tasks

        src = inspect.getsource(tasks)
        assert "import invalidate as _invalidate_snapshot" in src

    def test_defect_promotion_marks_stale(self):
        # Cheap staleness: the defect candidates moved, the rest of the payload
        # is still right, and serving it while a refresh converges is fair.
        import inspect

        from app.routers import deep_investigation

        src = inspect.getsource(deep_investigation)
        assert "import mark_stale" in src

    def test_a_release_override_deletes_rather_than_marking_stale(self):
        """The contract the old file got backwards.

        A human has just written GO -> NO_GO. A row still saying GO must not be
        servable at all, so the override deletes -- and stages the delete, so it
        lands in the same transaction as the override itself.
        """
        import inspect

        from app.routers import release_readiness

        src = inspect.getsource(release_readiness)
        assert "import stage_invalidate" in src
        assert "import mark_stale" not in src, (
            "the override marks the snapshot stale again, leaving the "
            "superseded verdict servable"
        )

    def test_exports_go_through_get_or_compute(self):
        import inspect

        from app.services import evidence_bundle_service, report_composition_service

        for module in (report_composition_service, evidence_bundle_service):
            src = inspect.getsource(module)
            assert "get_or_compute" in src, module.__name__
            assert "select(RunIntelligenceSnapshot)" not in src, module.__name__


class TestPrimitiveSemantics:
    @pytest.mark.asyncio
    async def test_mark_stale_reports_false_when_nothing_matched(self):
        assert await svc.mark_stale(_Db(None), RUN) is False

    @pytest.mark.asyncio
    async def test_mark_stale_sets_the_flag_and_reports_true(self):
        row = _row(schema_version=svc.CURRENT_SCHEMA_VERSION, stale=False)
        assert await svc.mark_stale(_Db(row), RUN) is True
        assert row.stale is True

    @pytest.mark.asyncio
    async def test_marking_an_already_stale_row_is_idempotent(self):
        row = _row(schema_version=svc.CURRENT_SCHEMA_VERSION, stale=True)
        assert await svc.mark_stale(_Db(row), RUN) is True
        assert row.stale is True

    @pytest.mark.asyncio
    async def test_mark_stale_does_not_commit(self):
        # Stage-only: the caller owns the commit so the flip lands atomically
        # with whatever triggered it.
        db = _Db(_row(schema_version=svc.CURRENT_SCHEMA_VERSION))
        await svc.mark_stale(db, RUN)
        assert db.committed is False

    @pytest.mark.asyncio
    async def test_invalidate_commits_and_reports_whether_it_deleted(self):
        db = _Db(rowcount=1)
        assert await svc.invalidate(db, RUN) is True
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_double_invalidation_is_safe(self):
        # Second call deletes nothing. The old test asserted isinstance(False, bool).
        db = _Db(rowcount=0)
        assert await svc.invalidate(db, RUN) is False

    @pytest.mark.asyncio
    async def test_stage_invalidate_deletes_without_committing(self):
        db = _Db(rowcount=1)
        assert await svc.stage_invalidate(db, RUN) is True
        assert db.committed is False, (
            "committing here would let a rolled-back override drop a valid snapshot"
        )

    @pytest.mark.asyncio
    async def test_stage_invalidate_reports_false_when_there_was_nothing(self):
        assert await svc.stage_invalidate(_Db(rowcount=0), RUN) is False


class TestInvalidationNeverBreaksItsCaller:
    """The old file asserted ``errors_swallowed = True``. These check the
    callers actually guard the call, since a failed invalidation must not take
    down the pipeline, the promotion or the override that triggered it."""

    @pytest.mark.parametrize(
        "module_path,needle",
        [
            ("app.routers.release_readiness", "stage_invalidate"),
            ("app.routers.deep_investigation", "mark_stale"),
        ],
    )
    def test_the_call_is_wrapped(self, module_path, needle):
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module(module_path))
        # Anchor on the CALL, not the name: both modules explain the choice in
        # a comment above it, and matching the first mention found the prose.
        call = f"await {needle}("
        idx = src.index(call)
        window = src[max(0, idx - 300):idx]
        assert "try:" in window, f"{module_path} calls {needle} outside a try"
        assert "except" in src[idx:idx + 400], (
            f"{module_path} does not handle a failing {needle}"
        )
