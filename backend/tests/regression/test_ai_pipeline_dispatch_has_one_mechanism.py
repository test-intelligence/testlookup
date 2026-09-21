"""One dispatch mechanism for the AI pipeline, and /health reports it — BUG-010.

Two mechanisms existed. Only one ran.

``ai_pipeline_debouncer`` offered a Redis SortedSet drained every two minutes by
a Celery beat task. Its entry point, ``enqueue_pipeline_for_run``, had **zero
production callers** — the sole non-test mention outside the module was a
commented-out line in ``celery_app.py``. Everything else in the module was
reachable only through it:

* ``_enqueue_direct`` — called only by ``enqueue_pipeline_for_run``
* ``flush_pending`` — called only by the beat task, draining a set nothing filled
* ``get_degraded_project_count`` — read by ``/health``, but the keys it counted
  were written only inside ``flush_pending``

So ``/health`` published two numbers that were *structurally incapable* of being
non-zero, while the mechanism that actually dispatches — ``run_downstream_outbox``
— went unreported. Measured: 279 consecutive flushes of an empty set while five
outbox rows waited.

``config.py`` meanwhile told operators that ``stream_service.close_session`` "no
longer fires ``run_agent_pipeline`` directly; the run lands in a Redis SortedSet",
and exposed ``AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS`` to tune it. ``stream_service``
referenced neither name. The knob did nothing.

The tracker deferred this as an owner decision — outbox or debouncer — on the
grounds that a branch was actively wiring the debouncer up. That branch's last
commit was 2026-07-15 and it sat **1374 commits behind main**; it was abandoned,
not active. With that removed, there was no decision left to make: the outbox is
what runs, and wiring the debouncer would have risked double dispatch.
"""
from __future__ import annotations

import importlib

import pytest


class TestTheDeadMechanismIsGone:
    def test_the_debouncer_module_no_longer_exists(self):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.services.ai_pipeline_debouncer")

    def test_no_beat_entry_drains_an_empty_queue(self):
        from app.worker.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule or {}
        assert "flush-ai-pipeline-queue" not in schedule, (
            "the beat task is back; it fired every 2 minutes against a set "
            "nothing writes"
        )
        tasks = {entry.get("task") for entry in schedule.values()}
        assert "app.worker.tasks.flush_ai_pipeline_queue" not in tasks

    def test_the_task_is_gone_from_the_worker(self):
        from app.worker import tasks

        assert not hasattr(tasks, "flush_ai_pipeline_queue")

    def test_the_settings_that_tuned_nothing_are_gone(self):
        from app.core.config import Settings

        for name in ("AI_PIPELINE_DEBOUNCE_ENABLED", "AI_PIPELINE_DEBOUNCE_WINDOW_SECONDS"):
            assert name not in Settings.model_fields, (
                f"{name} is back; it advertises a debounce that does not happen"
            )


class TestHealthReportsTheMechanismThatRuns:
    @pytest.mark.asyncio
    async def test_it_counts_pending_and_sending_rows(self):
        from app.services.run_downstream_outbox import pending_dispatch_count

        captured = {}

        class _Db:
            async def execute(self, stmt):
                captured["sql"] = str(stmt)
                captured["params"] = stmt.compile().params

                class _R:
                    @staticmethod
                    def scalar():
                        return 5

                return _R()

        assert await pending_dispatch_count(_Db()) == 5
        sql = captured["sql"].lower()
        assert "run_downstream_outbox" in sql
        # A backlog is work NOT YET DONE, so a row already leased to a worker
        # that has not finished still counts. Narrowing this to "pending" alone
        # under-reports exactly when the system is struggling -- and a bare
        # `"status" in sql` assertion did not notice that mutation.
        assert "in (" in sql or "in(" in sql, (
            f"expected a multi-status filter, got: {sql}"
        )
        # An IN clause binds ONE param holding a list, so the values have to be
        # flattened before they can be compared.
        params = set()
        for value in captured["params"].values():
            if isinstance(value, (list, tuple, set)):
                params.update(str(v).lower() for v in value)
            else:
                params.add(str(value).lower())
        assert {"pending", "sending"} <= params, (
            f"the query no longer counts both waiting and in-flight rows: {params}"
        )

    @pytest.mark.asyncio
    async def test_an_unreadable_backlog_is_none_not_zero(self):
        """The whole reason the old fields were harmful.

        Zero reads as "nothing is waiting". This endpoint exists for an on-call
        engineer asking why runs are slow, and it must not answer with the most
        reassuring value it could hold when it could not look.
        """
        from app.services.run_downstream_outbox import pending_dispatch_count

        class _DeadDb:
            async def execute(self, _stmt):
                raise RuntimeError("database unreachable")

        assert await pending_dispatch_count(_DeadDb()) is None

    @pytest.mark.asyncio
    async def test_health_reports_none_when_the_session_itself_fails(self):
        """The branch a mocked helper never reaches.

        The sibling test patches ``pending_dispatch_count``, so it exercises the
        helper's own failure path and never the ``except`` around the session
        this endpoint opens. Mutating that fallback from ``None`` to ``0``
        therefore survived every test -- and ``0`` on this field reads as "the
        queue is empty" to the on-call engineer who came to ask why runs are
        slow.
        """
        from unittest.mock import MagicMock, patch

        from app.routers import health

        # ONLY the session is broken. Breaking Redis too takes down paths this
        # endpoint does not guard, and the exception escapes before the line
        # under test runs -- which is what the first version of this test did.
        def _explode(*_a, **_kw):
            raise RuntimeError("pool exhausted")

        with patch("app.db.postgres.AsyncSessionLocal", MagicMock(side_effect=_explode)):
            payload = await health.health_ingestion()

        assert payload["ai_pipeline"]["pending_dispatches"] is None, (
            "a backlog that could not be read was reported as an empty queue"
        )

    def test_health_publishes_the_outbox_not_the_debouncer(self):
        import inspect

        from app.routers import health

        src = inspect.getsource(health.health_ingestion)
        assert "pending_dispatch_count" in src
        assert "pending_dispatches" in src
        for dead in ("pending_in_debouncer", "debouncer_enabled",
                     "debounce_window_seconds", "ai_pipeline_debouncer"):
            assert dead not in src, f"{dead} is back on the health payload"


class TestNothingStillDescribesTheDebouncer:
    def test_config_makes_no_claim_about_a_sortedset(self):
        import inspect

        from app.core import config

        src = inspect.getsource(config)
        assert "SortedSet" not in src, (
            "config still describes a debounce mechanism that does not exist; "
            "an operator reading this tunes a knob with no effect"
        )
