"""The run's AI verdict as a column: ``test_runs.ai_recommendation`` (W07 FAIL-05).

The Runs list gains an AI-verdict filter (``GET /runs?recommendation=``). The
verdict a row shows is computed from the run's intelligence snapshot
(``run_verdict.ai_recommendation_of``: a verified decision report's
recommendation, else -- when no report envelope exists -- the persisted release
decision), and a filter cannot compute that over JSON for every run of a
window. So the snapshot's writers project it into a nullable ``String(20)``:
GO | CONDITIONAL_GO | NO_GO, or NULL for "no verdict".

1. The column: nullable, no default -- a catalogue change, no table rewrite.
2. The backfill from every SERVABLE snapshot (``schema_version >= 5``), 0171's
   batched pattern: in ``autocommit_block`` so each batch commits and releases
   its row locks, keyset-paged by ``run_id`` over ``ix_ris_run_id``, each batch
   ONE ``UPDATE``. ``_RECOMMENDATION_SQL`` is the FROZEN SQL twin of
   ``ai_recommendation_of`` at this revision (``tests/integration/
   test_migration_0214_postgres.py`` binds it to the live function); the payload
   is cast to ``jsonb`` once per row. Every write keeps ``updated_at`` where it
   was: ``live_run_recovery_service`` picks candidates by ``updated_at``, and a
   bump would make every analysed live run a recovery candidate again.
3. The index ``(project_id, ai_recommendation, created_at)`` on the EXISTING
   table, CONCURRENTLY (the 0166+ rule), an INVALID leftover of a failed build
   dropped first (the 0211 / 0213 pattern).

The downgrade drops the index (CONCURRENTLY) and the column.

Revision ID: 0214
Revises: 0213
"""
import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

revision = "0214"
down_revision = "0213"
branch_labels = None
depends_on = None

TABLE = "test_runs"
COLUMN = "ai_recommendation"
INDEX = "ix_test_runs_project_recommendation_created"
_BATCH = 1000
# FROZEN copy of intelligence_snapshot_service.CURRENT_SCHEMA_VERSION at this
# revision: an older snapshot is not servable, so it projects nothing.
_SCHEMA_VERSION = 5


def _truthy(expr: str) -> str:
    """JavaScript's ``Boolean(x)`` over a jsonb value (FROZEN copy of the batch read's rule)."""
    return (
        f"(CASE jsonb_typeof({expr}) "
        "WHEN 'object' THEN TRUE WHEN 'array' THEN TRUE "
        f"WHEN 'string' THEN ({expr} #>> '{{}}') <> '' "
        f"WHEN 'number' THEN ({expr} #>> '{{}}')::numeric <> 0 "
        f"WHEN 'boolean' THEN ({expr} #>> '{{}}')::boolean "
        "ELSE FALSE END)"
    )


# The payload as jsonb, parsed once per row (FROZEN copy of
# run_verdict_service.JSONB_PAYLOAD_SQL): a json value may hold a \u0000 escape
# that jsonb refuses, and one such payload would fail the whole upgrade, so each
# \u0000 escape is first rewritten to the same-length   (still valid JSON).
_PAYLOAD = "CAST(replace(CAST(s.payload AS text), E'\\\\u0000', E'\\\\u0020') AS jsonb)"

_DI = "(j.p #> '{structured_summary,decision_intelligence}')"
_VER = "(j.p #> '{structured_summary,decision_report_verification}')"
_ATT = "(j.p #> '{structured_summary,latest_decision_attempt}')"
_RD = "(j.p -> 'release_decision')"
_VALUES = "('GO', 'CONDITIONAL_GO', 'NO_GO')"

# FROZEN SQL twin of run_verdict.ai_recommendation_of: a displayable report's
# recommendation; else NULL when any report envelope is truthy; else the
# release decision's.
_RECOMMENDATION_SQL = (
    "CASE "
    f"WHEN {_DI} #>> '{{verification,status}}' = 'passed' "
    f"AND (({_ATT} ->> 'status' = 'published' AND {_ATT} ->> 'verification_status' = 'passed' "
    f"AND {_VER} ->> 'status' = 'passed') "
    f"OR ({_ATT} ->> 'status' = 'rejected' AND {_ATT} ->> 'verification_status' = 'failed' "
    f"AND {_VER} ->> 'status' = 'failed')) "
    f"AND {_DI} #>> '{{release_decision,recommendation}}' IN {_VALUES} "
    f"THEN {_DI} #>> '{{release_decision,recommendation}}' "
    f"WHEN {_truthy(_DI)} OR {_truthy(_VER)} OR {_truthy(_ATT)} THEN NULL "
    f"WHEN {_RD} ->> 'recommendation' IN {_VALUES} THEN {_RD} ->> 'recommendation' "
    "END"
)

_NEXT_KEYS = sa.text(
    "SELECT run_id FROM run_intelligence_snapshots "
    "WHERE schema_version >= :v "
    "AND (CAST(:after AS uuid) IS NULL OR run_id > CAST(:after AS uuid)) "
    "ORDER BY run_id LIMIT :batch"
)

_FILL = sa.text(
    f"UPDATE {TABLE} AS tr SET {COLUMN} = {_RECOMMENDATION_SQL}, updated_at = tr.updated_at "
    f"FROM run_intelligence_snapshots AS s CROSS JOIN LATERAL (SELECT {_PAYLOAD} AS p) AS j "
    "WHERE s.run_id = tr.id AND s.run_id = ANY(CAST(:ids AS uuid[])) AND s.schema_version >= :v"
)


def backfill(bind, batch: int = _BATCH) -> tuple[int, int]:
    """Project every servable snapshot into ``ai_recommendation``; returns (runs updated, snapshots seen).

    Each statement commits on its own when ``bind`` is in autocommit, as it is
    inside ``autocommit_block``. The ids are bound, never spliced or logged.
    """
    after = None
    seen = updated = 0
    while True:
        keys = [
            str(k)
            for k in bind.execute(
                _NEXT_KEYS, {"v": _SCHEMA_VERSION, "after": after, "batch": batch}
            ).scalars()
        ]
        if not keys:
            return updated, seen
        updated += bind.execute(_FILL, {"ids": keys, "v": _SCHEMA_VERSION}).rowcount
        seen += len(keys)
        after = keys[-1]


def upgrade() -> None:
    # A nullable column with no default: a catalogue change, no table rewrite.
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(20), nullable=True))
    if op.get_context().as_sql:
        # Offline: one statement, the same result, without the batching.
        op.execute(
            f"UPDATE {TABLE} AS tr SET {COLUMN} = {_RECOMMENDATION_SQL}, updated_at = tr.updated_at "
            f"FROM run_intelligence_snapshots AS s CROSS JOIN LATERAL (SELECT {_PAYLOAD} AS p) AS j "
            f"WHERE s.run_id = tr.id AND s.schema_version >= {_SCHEMA_VERSION}"
        )
    else:
        with op.get_context().autocommit_block():
            updated, seen = backfill(op.get_bind())
        logger.info(
            "0214: ai_recommendation filled for %s of %s snapshot(s)", updated, seen
        )
    # An index on an existing table is built CONCURRENTLY (the 0166+ rule,
    # tests/regression/test_migration_index_builds_are_concurrent.py); an INVALID
    # leftover of a failed build is dropped first.
    invalid = False if op.get_context().as_sql else _index_is_invalid(op.get_bind())
    with op.get_context().autocommit_block():
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON {TABLE} (project_id, {COLUMN}, created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
    op.drop_column(TABLE, COLUMN)


def _index_is_invalid(bind) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": INDEX},
        ).first()
    )
