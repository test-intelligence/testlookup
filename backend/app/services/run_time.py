"""When a test run happened, for every chart bucket and time window (D-19).

A run has two clocks:

* ``start_time`` -- when it EXECUTED, as the reporter said (bounded by
  ``execution_time.resolve_execution_time``: never in the future, never more
  than a year back). ``POST /ingest/file`` and the JSON batch route take it
  as ``executed_at``; live and SDK runs stamp it at their start.
* ``created_at`` -- when TestLookup RECEIVED the row (server default).

Charts and windows used to read ``created_at``, so a backfilled archive, or a
nightly report uploaded the next morning, landed on its upload day in every
chart and every "last N days" window. The run time is the execution time when
the run has one, else the receive time:

    coalesce(test_runs.start_time, test_runs.created_at)

It is defined ONCE here, as an ORM expression and as its raw-SQL twin for the
statements that alias ``test_runs`` as ``tr``. Migration 0259 indexes exactly
this expression after ``project_id`` (``ix_test_runs_project_run_time``);
Postgres uses an expression index only when the query spells the same
expression, so neither form may be rewritten by hand at a call site.

``created_at`` keeps the orderings whose documented meaning is "received last
first" (``runs_service.run_list_newest_order_keys``) and the retention,
deletion and "earlier run" comparisons, which are about rows, not executions.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from app.models.postgres import TestRun

#: The raw-SQL run time for a statement that aliases ``test_runs`` as ``tr``.
#: Spelled exactly as the index expression of migration 0259.
RUN_TIME_SQL = "COALESCE(tr.start_time, tr.created_at)"


def run_time_column() -> ColumnElement:
    """``coalesce(test_runs.start_time, test_runs.created_at)`` for ORM queries."""
    return func.coalesce(TestRun.start_time, TestRun.created_at)
