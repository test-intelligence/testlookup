"""A run's current failure clusters: the newest pipeline's snapshot.

``persist_failure_cluster_snapshot`` keeps one immutable cluster set per
producing pipeline run -- investigations (``agent_investigations``) and stage
results reference those rows -- so a run analysed twice holds two sets and
every cluster id twice. Readers that asked for "the run's clusters" by
``test_run_id`` alone showed each cluster twice, counted it twice in project
metrics, and a lookup by ``cluster_id`` raised ``MultipleResultsFound``
(homelab, 2026-10-14).

``is_current_snapshot()`` keeps only the rows of each run's newest set. It is
correlated on ``FailureCluster.test_run_id``, so it works for one run and for
queries across many. Older sets stay in the table for the history that
references them; a reader that wants one pipeline's set filters on
``pipeline_run_id`` instead.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.postgres import FailureCluster


def is_current_snapshot() -> ColumnElement[bool]:
    """True for the rows of their run's newest cluster snapshot.

    A snapshot is written in one transaction, so its rows share
    ``created_at``; the newest row's ``pipeline_run_id`` names the snapshot.
    Seed rows have no pipeline (NULL), hence IS NOT DISTINCT FROM.
    """
    newer = aliased(FailureCluster)
    newest_pipeline = (
        select(newer.pipeline_run_id)
        .where(newer.test_run_id == FailureCluster.test_run_id)
        .order_by(newer.created_at.desc(), newer.id.desc())
        .limit(1)
        .correlate(FailureCluster)
        .scalar_subquery()
    )
    return FailureCluster.pipeline_run_id.is_not_distinct_from(newest_pipeline)


def current_snapshot_sql(alias: str) -> str:
    """``is_current_snapshot()`` for a raw-SQL query on ``failure_clusters <alias>``."""
    return (
        f"{alias}.pipeline_run_id IS NOT DISTINCT FROM ("
        "SELECT newer.pipeline_run_id FROM failure_clusters newer "
        f"WHERE newer.test_run_id = {alias}.test_run_id "
        "ORDER BY newer.created_at DESC, newer.id DESC LIMIT 1)"
    )
