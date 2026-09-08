"""The service that repairs silent data loss had no test of its own.

``services/live_run_recovery_service.py`` measured **0% coverage** — one of
only four modules in the backend with nothing at all, and the one that matters
most, because of what it is for.

It exists as belt-and-braces for the ``close_session -> persist_live_session``
handoff. When that handoff fails (a swallowed ``apply_async``, queue
backpressure, a worker restart mid-dispatch) the ``TestRun`` row keeps its
final aggregates while ``test_cases`` stays empty, and the user sees
``[ingestion gap …]`` placeholder rows instead of their test names. This sweep
finds those runs and re-queues persistence from ``event_archive``.

A recovery mechanism with no tests fails in the worst possible way: silently,
and only at the moment you need it — after the primary path has *already*
failed. Nothing would have caught it, because the symptom is identical to "no
runs needed recovery".

The cases below are the decisions the sweep makes, not the plumbing around
them: which runs it declines, when it must not re-stage, and whether one bad
run can take the sweep down with it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import live_run_recovery_service as svc

pytestmark = pytest.mark.regression


class _FakeRedis:
    """Records Stream staging; lengths are scripted per Redis key."""

    def __init__(self, lengths: dict[str, int] | None = None):
        self._lengths = lengths or {}
        self.pushed: list[tuple[str, str]] = []
        self.expires: list[tuple[str, int]] = []

    async def llen(self, key):
        return self._lengths.get(key, 0)

    async def xlen(self, key):
        return self._lengths.get(key, 0)

    async def eval(self, *_args):
        key = _args[2]
        value = {
            "batch_digest": _args[12],
            "event_count": _args[13],
            "batch_id": _args[14],
            "session_id": _args[15],
            "run_id": _args[16],
            "events_json": _args[17],
            "event_ids_json": _args[18],
            "trim_legacy": "0",
        }
        self.pushed.append((key, value))
        return ["accepted", value["event_count"], "1-0"]

    async def expire(self, key, ttl):
        self.expires.append((key, ttl))
        return True


def _run(**overrides):
    """A live_stream TestRun row shaped the way the sweep reads it."""
    base = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        build_number="42",
        branch="main",
        commit_hash="abc123",
        event_archive=[{"test": "a"}, {"test": "b"}],
        passed_tests=1,
        failed_tests=1,
        skipped_tests=0,
        broken_tests=0,
        unknown_tests=0,
        total_tests=2,
        primary_suite_name="Smoke",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db(candidates):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = candidates
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def dispatched(monkeypatch):
    """Capture every persist_live_session.apply_async call."""
    calls: list[dict] = []
    task = MagicMock()
    task.apply_async = MagicMock(side_effect=lambda **kw: calls.append(kw))

    tasks_mod = SimpleNamespace(persist_live_session=task)
    routing_mod = SimpleNamespace(queue_for_project=lambda pid: "ingestion.shard.0")
    monkeypatch.setitem(__import__("sys").modules, "app.worker.tasks", tasks_mod)
    monkeypatch.setitem(__import__("sys").modules, "app.worker.ingestion_routing", routing_mod)
    return calls


class TestWhatItDeclines:
    @pytest.mark.asyncio
    async def test_no_candidates_reports_zeroes_rather_than_failing(self, monkeypatch, dispatched):
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        counts = await svc.auto_recover_completed_runs(_db([]))

        assert counts == {"candidates": 0, "recovered": 0, "errors": 0}
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_a_run_with_an_empty_archive_is_counted_but_not_recovered(
        self, monkeypatch, dispatched
    ):
        """`event_archive_at` is set but the column holds nothing — there are no
        real names to restore, so it is left to the placeholder backfill rather
        than dispatched into a no-op."""
        redis = _FakeRedis()
        monkeypatch.setattr(svc, "get_redis", lambda: redis)

        counts = await svc.auto_recover_completed_runs(_db([_run(event_archive=[])]))

        assert counts["candidates"] == 1
        assert counts["recovered"] == 0
        assert dispatched == []
        assert redis.pushed == [], "nothing should be staged for an empty archive"

    @pytest.mark.asyncio
    async def test_a_null_archive_is_treated_like_an_empty_one(self, monkeypatch, dispatched):
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        counts = await svc.auto_recover_completed_runs(_db([_run(event_archive=None)]))

        assert counts["recovered"] == 0
        assert dispatched == []


class TestTheDoubleInsertGuard:
    """The rule with a real hazard behind it: ``persist_live_session`` reads the
    same Redis list this sweep would RPUSH into. Staging the archive on top of a
    still-populated buffer doubles every row."""

    @pytest.mark.asyncio
    async def test_a_populated_buffer_is_used_instead_of_re_staging(
        self, monkeypatch, dispatched
    ):
        run = _run()
        key = svc.LIVE_TESTCASES_KEY.format(run_id=str(run.id))
        redis = _FakeRedis({key: 7})
        monkeypatch.setattr(svc, "get_redis", lambda: redis)

        counts = await svc.auto_recover_completed_runs(_db([run]))

        assert counts["recovered"] == 1
        assert redis.pushed == [], (
            "the archive was re-staged on top of a live buffer — every test row "
            "would be persisted twice"
        )

    @pytest.mark.asyncio
    async def test_an_empty_buffer_is_restaged_from_the_archive(
        self, monkeypatch, dispatched
    ):
        run = _run(event_archive=[{"test": "a"}, {"test": "b"}, {"test": "c"}])
        redis = _FakeRedis()  # llen -> 0
        monkeypatch.setattr(svc, "get_redis", lambda: redis)

        counts = await svc.auto_recover_completed_runs(_db([run]))

        assert counts["recovered"] == 1
        assert len(redis.pushed) == 1, "the archive must be one atomic batch record"
        expected_key = svc.LIVE_EVIDENCE_STREAM_KEY.format(run_id=str(run.id))
        assert {k for k, _ in redis.pushed} == {expected_key}

    @pytest.mark.asyncio
    async def test_archive_staging_uses_the_durable_stream_without_a_ttl(
        self, monkeypatch, dispatched
    ):
        """Recovery evidence must survive until the drainer commits it."""
        run = _run()
        redis = _FakeRedis()
        monkeypatch.setattr(svc, "get_redis", lambda: redis)

        await svc.auto_recover_completed_runs(_db([run]))

        expected_key = svc.LIVE_EVIDENCE_STREAM_KEY.format(run_id=str(run.id))
        assert {key for key, _ in redis.pushed} == {expected_key}
        assert redis.expires == []


class TestTheDispatch:
    @pytest.mark.asyncio
    async def test_the_final_state_carries_the_run_aggregates(self, monkeypatch, dispatched):
        """The aggregates survived the failed handoff; they are what makes the
        recovered run reconcile with what the user already saw."""
        run = _run(
            passed_tests=5,
            failed_tests=2,
            skipped_tests=1,
            broken_tests=3,
            unknown_tests=4,
            total_tests=15,
        )
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        await svc.auto_recover_completed_runs(_db([run]))

        assert len(dispatched) == 1
        kwargs = dispatched[0]["kwargs"]
        assert kwargs["final_state"] == {
            "passed": 5,
            "failed": 2,
            "skipped": 1,
            "broken": 3,
            "unknown": 4,
            "total": 15,
        }
        assert kwargs["run_id"] == str(run.id)
        assert kwargs["project_id"] == str(run.project_id)

    @pytest.mark.asyncio
    async def test_missing_aggregates_become_zero_not_none(self, monkeypatch, dispatched):
        """`persist_live_session` does arithmetic on these. None would raise
        inside the worker, where the failure is a retry loop, not a 500."""
        run = _run(
            passed_tests=None,
            failed_tests=None,
            skipped_tests=None,
            broken_tests=None,
            unknown_tests=None,
            total_tests=None,
        )
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        await svc.auto_recover_completed_runs(_db([run]))

        assert dispatched[0]["kwargs"]["final_state"] == {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "broken": 0,
            "unknown": 0,
            "total": 0,
        }

    @pytest.mark.asyncio
    async def test_a_missing_build_number_falls_back_to_the_run_id(
        self, monkeypatch, dispatched
    ):
        run = _run(build_number=None)
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        await svc.auto_recover_completed_runs(_db([run]))

        assert dispatched[0]["kwargs"]["build_number"] == str(run.id)

    @pytest.mark.asyncio
    async def test_it_routes_to_the_project_shard(self, monkeypatch, dispatched):
        """Routing is the producer's decision, not the broker's."""
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        await svc.auto_recover_completed_runs(_db([_run()]))

        assert dispatched[0]["queue"] == "ingestion.shard.0"


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_one_failing_run_does_not_abort_the_sweep(self, monkeypatch, dispatched):
        """A sweep that dies on its first bad row leaves every later run
        unrecovered — and reports success, because the exception is the only
        signal and nobody is reading it."""
        good_a, bad, good_b = _run(), _run(), _run()
        bad_key = svc.LIVE_TESTCASES_KEY.format(run_id=str(bad.id))

        class _Redis(_FakeRedis):
            async def llen(self, key):
                if key == bad_key:
                    raise RuntimeError("redis went away")
                return 0

        monkeypatch.setattr(svc, "get_redis", lambda: _Redis())

        counts = await svc.auto_recover_completed_runs(_db([good_a, bad, good_b]))

        assert counts["candidates"] == 3
        assert counts["recovered"] == 2, "the runs either side of the failure must still go"
        assert counts["errors"] == 1

    @pytest.mark.asyncio
    async def test_a_dispatch_failure_is_counted_not_raised(self, monkeypatch, dispatched):
        """The caller is an hourly beat task. Raising turns a partial sweep into
        a retry of the whole thing."""
        task = MagicMock()
        task.apply_async = MagicMock(side_effect=RuntimeError("broker down"))
        monkeypatch.setitem(
            __import__("sys").modules, "app.worker.tasks",
            SimpleNamespace(persist_live_session=task),
        )
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())

        counts = await svc.auto_recover_completed_runs(_db([_run()]))

        assert counts == {"candidates": 1, "recovered": 0, "errors": 1}


class TestTheTransactionContract:
    @pytest.mark.asyncio
    async def test_it_never_commits(self, monkeypatch, dispatched):
        """Documented contract: "Caller owns the transaction. The function does
        not commit." A service that commits an injected session ends the
        caller's unit of work early — the transaction-boundary rule this repo
        enforces in `test_architectural_transaction_boundaries.py`."""
        monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())
        db = _db([_run()])

        await svc.auto_recover_completed_runs(db)

        db.commit.assert_not_called()
        db.rollback.assert_not_called()


# ── The second sweep in this module ──────────────────────────────────────────
#
# ``repair_clobbered_primary_suite_names`` re-stamps ``TestRun.primary_suite_name``
# from the authoritative ``LiveSession.suite_name``. It exists because
# finalize_run used to recompute the label from the dominant per-event
# suite_name, which the TestNG listener stamped to the test CLASS name — so a
# run the user labelled "API Regression Multi-Class" surfaced as
# ``com.example.OrderApiRegressionTests``.
#
# It writes to rows, unlike its sibling, which makes its skip conditions the
# load-bearing part: a sweep that repairs too eagerly overwrites a label the
# user chose.


def _rows_db(rows, *, execute_error: Exception | None = None):
    """The repair sweep reads ``.all()`` tuples, not ``.scalars()``."""
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    if execute_error is not None:
        db.execute = AsyncMock(side_effect=execute_error)
    else:
        db.execute = AsyncMock(return_value=result)
    return db


class TestSuiteNameRepairSkips:
    """What it declines to touch. Each of these would overwrite a good label."""

    @pytest.mark.asyncio
    async def test_a_matching_label_is_left_alone(self):
        """The docstring's idempotency claim: "when the two already match, the
        row is left alone". Without this the sweep rewrites every live run on
        every hourly beat."""
        db = _rows_db([(uuid.uuid4(), "Smoke", "Smoke")])

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts == {"candidates": 1, "repaired": 0}
        assert db.execute.await_count == 1, "only the SELECT should have run"

    @pytest.mark.asyncio
    async def test_labels_differing_only_by_whitespace_are_the_same_label(self):
        """Both sides are stripped before comparison, so padding is not a
        difference worth a write."""
        db = _rows_db([(uuid.uuid4(), "  Smoke  ", "Smoke")])

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts["repaired"] == 0
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_a_blank_session_label_never_clobbers_a_real_one(self):
        """`LiveSession.suite_name` of "   " must not replace "Smoke" with
        nothing — the sweep exists to restore a label, not to erase one."""
        db = _rows_db([(uuid.uuid4(), "Smoke", "   ")])

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts["repaired"] == 0
        assert db.execute.await_count == 1


class TestSuiteNameRepairWrites:
    @pytest.mark.asyncio
    async def test_a_clobbered_label_is_restored_from_the_session(self):
        """The reported bug, in one row: the run shows the test class name and
        the session holds what the user actually chose."""
        db = _rows_db([(
            uuid.uuid4(),
            "com.example.OrderApiRegressionTests",
            "API Regression Multi-Class",
        )])

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts == {"candidates": 1, "repaired": 1}
        assert db.execute.await_count == 2, "SELECT then UPDATE"

    @pytest.mark.asyncio
    async def test_a_run_with_no_label_yet_gets_one(self):
        db = _rows_db([(uuid.uuid4(), None, "Smoke")])

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts["repaired"] == 1

    @pytest.mark.asyncio
    async def test_external_display_slug_does_not_drive_the_identity_join(self):
        """A client slug can differ from the internal LiveSession/TestRun UUID."""
        db = _rows_db([])

        await svc.repair_clobbered_primary_suite_names(db)

        statement = db.execute.await_args_list[0].args[0]
        sql = str(statement)
        assert "live_sessions.id = test_runs.id" in sql
        assert "live_sessions.run_id" not in sql

    @pytest.mark.asyncio
    async def test_it_repairs_each_differing_row_and_skips_the_rest(self):
        db = _rows_db([
            (uuid.uuid4(), "wrong", "right"),
            (uuid.uuid4(), "same", "same"),
            (uuid.uuid4(), None, "also-right"),
        ])

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts == {"candidates": 3, "repaired": 2}

    @pytest.mark.asyncio
    async def test_it_does_not_commit(self):
        """Same contract as its sibling: the caller owns the transaction. This
        one stages real UPDATEs, so committing here would end the caller's unit
        of work mid-sweep."""
        db = _rows_db([(uuid.uuid4(), "wrong", "right")])

        await svc.repair_clobbered_primary_suite_names(db)

        db.commit.assert_not_called()
        db.rollback.assert_not_called()


class TestSuiteNameRepairNeverFailsTheBeat:
    @pytest.mark.asyncio
    async def test_a_broken_join_returns_counts_instead_of_raising(self):
        """The CAST in the join is dialect-dependent (sqlite vs postgres), and
        the code says so: "never fail the beat task here — log and bail". The
        caller is an hourly beat that also runs the other sweep."""
        db = _rows_db([], execute_error=RuntimeError("no such function: CAST"))

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts == {"candidates": 0, "repaired": 0}

    @pytest.mark.asyncio
    async def test_one_failing_update_does_not_stop_the_others(self):
        """DOCUMENTED ASYMMETRY, pinned so it is a choice rather than a
        surprise. The per-row `except` here logs and continues, but this
        sweep's counts have no `errors` key — unlike its sibling
        `auto_recover_completed_runs`, which returns one. So a row that fails
        to repair is visible only in the log: the return value reports
        "candidates 2, repaired 1" and nothing says the difference was a
        failure rather than a skip.
        """
        first, second = uuid.uuid4(), uuid.uuid4()
        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = [(first, "wrong", "right"), (second, "wrong2", "right2")]

        calls = {"n": 0}

        async def _execute(stmt):
            calls["n"] += 1
            if calls["n"] == 1:
                return result          # the SELECT
            if calls["n"] == 2:
                raise RuntimeError("update blew up")
            return MagicMock()
        db.execute = _execute

        counts = await svc.repair_clobbered_primary_suite_names(db)

        assert counts["candidates"] == 2
        assert counts["repaired"] == 1, "the second row must still be attempted"
        assert "errors" not in counts
