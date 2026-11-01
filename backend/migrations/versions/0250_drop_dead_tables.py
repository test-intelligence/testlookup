"""Drop the four dead Postgres tables (W25d HYG-02).

No code path writes or reads these tables any more:

* ``contract_violations`` (0006) -- the deep investigation keeps its contract
  findings in the ``deep_findings.contract_violations`` JSONB COLUMN, which is
  NOT touched here;
* ``ai_provenance_records`` (0026, re-anchored by 0113) -- never written; the
  W22 retention purge that deleted its rows leaves with this release;
* ``tenant_metric_snapshots`` (0037) -- never written;
* ``quality_gates`` (0001, ``ix_quality_gates_project`` from 0082) -- never
  written; the W24 export stops listing it and the import skips an older
  archive's rows with a report line.

The upgrade PROBES before it drops: for each table that exists
(``to_regclass``; an install that never had one skips it) it counts the rows,
and if ANY table holds a row it refuses with a ``RuntimeError`` naming EVERY
non-empty table with its count -- before the first ``DROP``, inside the
migration's transaction, so nothing is dropped. Otherwise it drops the four in
``DEAD_TABLES`` order; their indexes, primary keys and foreign keys go with
them, and no other table references any of them. In offline (``--sql``) mode
there is no database to probe: the probe is skipped (the 0245 / 0249
precedent) and the script drops the four.

The downgrade recreates the four EMPTY tables with the shapes a 0249 database
holds -- copied from the DDL of 0001 / 0006 / 0026 + 0113 / 0037 / 0082, NOT
from the ORM (e.g. ``quality_gates.rules`` is ``JSONB DEFAULT '[]'`` in the
database and ``JSON`` in the old ORM class; ``contract_violations.severity``
keeps 0006's doubly quoted default; ``ai_provenance_records.project_id`` is
the column 0113 appended, with ``run_id`` ``ON DELETE SET NULL``). Every
constraint and index carries the name the original migrations gave it (the
foreign keys and primary keys the names Postgres generated). The proof is
``tests/integration/test_migration_0250_postgres.py``: the reflection of the
tables this downgrade creates equals a snapshot captured from a database at
0249.

Revision ID: 0250
Revises: 0249
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0250"
down_revision = "0249"
branch_labels = None
depends_on = None

#: Probe order and drop order. No table references another.
DEAD_TABLES = ("contract_violations", "ai_provenance_records", "tenant_metric_snapshots", "quality_gates")

# One literal per table (the names are module constants, never input).
_COUNT_SQL = {
    "contract_violations": "SELECT count(*) FROM contract_violations",
    "ai_provenance_records": "SELECT count(*) FROM ai_provenance_records",
    "tenant_metric_snapshots": "SELECT count(*) FROM tenant_metric_snapshots",
    "quality_gates": "SELECT count(*) FROM quality_gates",
}
_EXISTS_SQL = "SELECT to_regclass(:name) IS NOT NULL"


def upgrade() -> None:
    if op.get_context().as_sql:
        # Offline (--sql): no database to probe, so no refusal is possible here; the script drops all four.
        present = list(DEAD_TABLES)
    else:
        bind = op.get_bind()
        present = [name for name in DEAD_TABLES if _exists(bind, name)]
        counts = [(name, _row_count(bind, name)) for name in present]
        non_empty = [(name, count) for name, count in counts if count > 0]
        if non_empty:
            raise RuntimeError(_refusal(non_empty))
    for name in DEAD_TABLES:
        if name in present:
            op.drop_table(name)


def _exists(bind, name: str) -> bool:
    return bool(bind.execute(sa.text(_EXISTS_SQL), {"name": name}).scalar())


def _row_count(bind, name: str) -> int:
    return int(bind.execute(sa.text(_COUNT_SQL[name])).scalar() or 0)


def _refusal(non_empty: list[tuple[str, int]]) -> str:
    named = ", ".join(f"{name} has {count} {'row' if count == 1 else 'rows'}" for name, count in non_empty)
    return (
        f"0250_drop_dead_tables refused: {named} — nothing was dropped. "
        "Export or delete those rows deliberately, then upgrade again."
    )


def downgrade() -> None:
    # The 0249 shapes, in reverse drop order; each table is new here, so its indexes are plain builds.
    # quality_gates: 0001, + ix_quality_gates_project (0082).
    op.create_table(
        "quality_gates",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("rules", JSONB, server_default="[]", nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true", nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="quality_gates_pkey"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="quality_gates_project_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_quality_gates_project", "quality_gates", ["project_id"])

    # tenant_metric_snapshots: 0037.
    op.create_table(
        "tenant_metric_snapshots",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("total_runs", sa.Integer(), server_default="0", nullable=True),
        sa.Column("total_tests", sa.Integer(), server_default="0", nullable=True),
        sa.Column("avg_pass_rate", sa.Float(), nullable=True),
        sa.Column("failed_runs", sa.Integer(), server_default="0", nullable=True),
        sa.Column("ai_analyses_count", sa.Integer(), server_default="0", nullable=True),
        sa.Column("release_decisions_count", sa.Integer(), server_default="0", nullable=True),
        sa.Column("audit_events_count", sa.Integer(), server_default="0", nullable=True),
        sa.PrimaryKeyConstraint("id", name="tenant_metric_snapshots_pkey"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="tenant_metric_snapshots_project_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tms_project_time", "tenant_metric_snapshots", ["project_id", "recorded_at"])

    # ai_provenance_records: 0026, then 0113 (run_id FK CASCADE -> SET NULL; project_id appended, FK CASCADE, indexed).
    op.create_table(
        "ai_provenance_records",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("confidence_reason", sa.Text(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("sources_used", sa.JSON(), nullable=True),
        sa.Column("deterministic_checks_used", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="ai_provenance_records_pkey"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["test_runs.id"], name="ai_provenance_records_run_id_fkey", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="ai_provenance_records_project_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_provenance_entity", "ai_provenance_records", ["entity_type", "entity_id"])
    op.create_index("ix_provenance_run", "ai_provenance_records", ["run_id"])
    op.create_index("ix_provenance_project", "ai_provenance_records", ["project_id"])

    # contract_violations: 0006 (severity keeps 0006's server_default "'warning'", quotes included).
    op.create_table(
        "contract_violations",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("test_run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("test_case_id", UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("violation_type", sa.String(50), nullable=False),
        sa.Column("field_path", sa.String(500), nullable=True),
        sa.Column("expected", sa.String(500), nullable=True),
        sa.Column("actual", sa.String(500), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="'warning'"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id", name="contract_violations_pkey"),
        sa.ForeignKeyConstraint(
            ["test_run_id"], ["test_runs.id"], name="contract_violations_test_run_id_fkey", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["test_cases.id"], name="contract_violations_test_case_id_fkey", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_contract_violations_run", "contract_violations", ["test_run_id"])
    op.create_index("ix_contract_violations_tc", "contract_violations", ["test_case_id"])
