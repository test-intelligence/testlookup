"""Every AI analysis of a test result is kept as an immutable version (AIH-01).

``ai_analysis`` holds ONE row per test case (unique ``test_case_id``) and every
writer overwrites it: a re-analysis, a single-test analyze, a human correction
or a bulk reclassify lost the earlier answer. ``ai_analysis_versions`` keeps
each one, append-only, with its trigger (``pipeline`` / ``manual_analyze`` /
``human_correction`` / ``bulk_reclassify`` / ``backfill``; a code constant, no
CHECK), the pipeline or the user that produced it, and a ``content_sha256``.

* Dedup keys: ``UNIQUE (test_case_id, version)``; the partial unique index
  ``uq_ai_analysis_versions_case_pipeline`` on ``(test_case_id,
  pipeline_run_id)`` for pipeline versions makes a replayed pipeline (broker
  redelivery) insert nothing.
* ``test_run_id`` / ``project_id`` / ``test_fingerprint`` are copied so
  retention, export and the history lists need no join.
* Retention (owner decision D41): the RUNS clock. Rows die with their test
  case, run or project (ON DELETE CASCADE) and are capped per test result by
  ``project_retention_policies.ai_versions_per_test`` (migration 0206).

Backfill: every existing ``ai_analysis`` row becomes version 1 with trigger
``backfill``, in 256 ``INSERT ... SELECT`` statements (one per first byte of
``ai_analysis.id``: random UUIDv4s spread evenly and PostgreSQL compares uuids
bytewise, so the ranges partition the table), idempotent through ``ON CONFLICT
(test_case_id, version) DO NOTHING``. ``content_sha256`` is computed in SQL by
``ANALYSIS_CONTENT_SHA256_SQL``, a literal copy of the constant in
``app/services/ai_history_core.py`` (migrations never import app code; a unit
test asserts the two are equal, so SQL and Python hash byte for byte).

The run-level history (Mongo ``ai_run_versions``) is not created here: its
indexes come from ``app.db.mongo.ensure_indexes`` and existing runs are
versioned by the Celery task ``backfill_ai_run_versions``.

Downgrade drops the indexes and the table; Mongo is untouched.

Revision ID: 0205
Revises: 0204
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0205"
down_revision = "0204"
branch_labels = None
depends_on = None

TABLE = "ai_analysis_versions"

#: sha256 over the material fields of one analysis row, joined by U+001F.
#: ``{a}`` is the alias of an ``ai_analysis`` row. MUST equal
#: ``app.services.ai_history_core.ANALYSIS_CONTENT_SHA256_SQL`` (unit-tested).
ANALYSIS_CONTENT_SHA256_SQL = (
    "encode(sha256(convert_to(concat_ws(E'\\x1f', 'v1', "
    "coalesce({a}.failure_category, ''), "
    "coalesce(CAST({a}.confidence_score AS text), ''), "
    "CASE WHEN coalesce({a}.is_flaky, false) THEN '1' ELSE '0' END, "
    "CASE WHEN coalesce({a}.requires_human_review, false) THEN '1' ELSE '0' END, "
    "CASE WHEN coalesce({a}.backend_error_found, false) THEN '1' ELSE '0' END, "
    "CASE WHEN coalesce({a}.pod_issue_found, false) THEN '1' ELSE '0' END, "
    "coalesce({a}.llm_provider, ''), "
    "coalesce({a}.llm_model, ''), "
    "coalesce({a}.routing_metadata->>'analysis_mode', ''), "
    "coalesce({a}.root_cause_summary, '')), 'UTF8')), 'hex')"
)

_BACKFILL_SQL = (
    "INSERT INTO ai_analysis_versions (id, test_case_id, test_run_id, project_id, test_fingerprint, "
    "version, trigger, pipeline_run_id, actor_user_id, content_sha256, failure_category, "
    "confidence_score, root_cause_summary, is_flaky, backend_error_found, pod_issue_found, "
    "requires_human_review, recommended_actions, evidence_references, tools_used, role_actions, "
    "llm_provider, llm_model, analysis_mode, provenance, created_at) "
    "SELECT gen_random_uuid(), a.test_case_id, tc.test_run_id, tr.project_id, tc.test_fingerprint, "
    "1, 'backfill', NULL, NULL, " + ANALYSIS_CONTENT_SHA256_SQL.format(a="a") + ", "
    "a.failure_category, a.confidence_score, a.root_cause_summary, "
    "coalesce(a.is_flaky, false), coalesce(a.backend_error_found, false), "
    "coalesce(a.pod_issue_found, false), coalesce(a.requires_human_review, false), "
    "a.recommended_actions, a.evidence_references, a.tools_used, a.role_actions, "
    "a.llm_provider, a.llm_model, a.routing_metadata->>'analysis_mode', "
    "jsonb_strip_nulls(jsonb_build_object("
    "'analysis_mode', a.routing_metadata->>'analysis_mode', "
    "'mode_requested', a.routing_metadata->>'mode_requested', "
    "'fallback_from', a.routing_metadata->>'fallback_from', "
    "'fallback_reason', a.routing_metadata->>'fallback_reason', "
    "'prompt_versions', a.routing_metadata->'prompt_versions', "
    "'eval_manifest_checksum', a.routing_metadata->>'eval_manifest_checksum', "
    "'source', 'backfill')), "
    "coalesce(a.created_at, now()) "
    "FROM ai_analysis a "
    "JOIN test_cases tc ON tc.id = a.test_case_id "
    "JOIN test_runs tr ON tr.id = tc.test_run_id "
    "WHERE a.id >= CAST(:lo AS uuid){upper} "
    "ON CONFLICT (test_case_id, version) DO NOTHING"
)


def _bound(i: int) -> str:
    return f"{i:02x}000000-0000-0000-0000-000000000000"


def backfill_statements() -> list[tuple[str, dict]]:
    """The 256 ``(sql, params)`` chunks of the version-1 backfill, in order.

    Chunk ``i`` covers ``ai_analysis.id`` in ``[i0000000-..., (i+1)0000000-...)``;
    the last one is open-ended. Exposed so the Postgres integration test runs
    exactly this backfill against seeded rows at head.
    """
    statements: list[tuple[str, dict]] = []
    for i in range(256):
        if i < 255:
            sql = _BACKFILL_SQL.format(upper=" AND a.id < CAST(:hi AS uuid)")
            statements.append((sql, {"lo": _bound(i), "hi": _bound(i + 1)}))
        else:
            statements.append((_BACKFILL_SQL.format(upper=""), {"lo": _bound(i)}))
    return statements


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "test_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=24), nullable=False),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("failure_category", sa.String(length=30), nullable=True),
        sa.Column("confidence_score", sa.Integer(), nullable=True),
        sa.Column("root_cause_summary", sa.Text(), nullable=True),
        sa.Column("is_flaky", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("backend_error_found", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pod_issue_found", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recommended_actions", sa.JSON(), nullable=True),
        sa.Column("evidence_references", sa.JSON(), nullable=True),
        sa.Column("tools_used", sa.JSON(), nullable=True),
        sa.Column("role_actions", sa.JSON(), nullable=True),
        sa.Column("llm_provider", sa.String(length=50), nullable=True),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("analysis_mode", sa.String(length=20), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("test_case_id", "version", name="uq_ai_analysis_versions_case_version"),
        sa.CheckConstraint("version >= 1", name="ck_ai_analysis_versions_version"),
    )
    op.create_index("ix_ai_analysis_versions_run", TABLE, ["test_run_id"])
    # Raw SQL so the DESC ordering is applied (op.create_index builds ASC).
    op.execute(
        "CREATE INDEX ix_ai_analysis_versions_project_fp ON ai_analysis_versions "
        "(project_id, test_fingerprint, created_at DESC)"
    )
    op.create_index(
        "ix_ai_analysis_versions_pipeline",
        TABLE,
        ["pipeline_run_id"],
        postgresql_where=sa.text("pipeline_run_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ai_analysis_versions_case_pipeline",
        TABLE,
        ["test_case_id", "pipeline_run_id"],
        unique=True,
        postgresql_where=sa.text("trigger = 'pipeline' AND pipeline_run_id IS NOT NULL"),
    )
    for sql, params in backfill_statements():
        op.execute(sa.text(sql).bindparams(**params))


def downgrade() -> None:
    op.drop_index("uq_ai_analysis_versions_case_pipeline", table_name=TABLE)
    op.drop_index("ix_ai_analysis_versions_pipeline", table_name=TABLE)
    op.drop_index("ix_ai_analysis_versions_project_fp", table_name=TABLE)
    op.drop_index("ix_ai_analysis_versions_run", table_name=TABLE)
    op.drop_table(TABLE)
