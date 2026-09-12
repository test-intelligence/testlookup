"""Human review gate storage: review_requests, users.is_synthetic, a draft-distribution setting (E8.1).

Requirement 10 of the agentic architecture is that every AI-generated report is
a proposal until a human accepts it. This revision adds what that needs:

* ``review_requests`` -- one row per run (or, later, invocation) that produced a
  report. ``state`` is closed to ``pending_review accepted rejected superseded``.
  A partial unique index keeps exactly one LIVE request per subject: superseded
  rows stay as history, so "who accepted the report this run replaced" is still
  answerable. A rejection must carry a ``reason_code`` from a closed set -- it is
  the only part of a rejection that becomes an eval label (section 11.3) -- and
  a settled request must carry the time it was settled.
* ``users.is_synthetic`` -- the review gate must refuse accounts no human logs in
  as. Until now the only marker was the ``qa-lead.testlookup.local`` email
  domain the default QA-lead service uses, which any admin can type into a new
  account. Backfilled from that domain once, here; the service sets it directly
  from now on.
* ``projects.allow_unreviewed_distribution`` -- whether drafts may be exported or
  notified (section 8.2). Default off. Enforced in E8.4; stored now so the
  setting exists before anything reads it.

``review_requests`` is new, so its indexes are built in the ordinary way (the
concurrent-build rule covers tables a migration did not create). The two new
columns carry no index.

Downgrade drops the table and both columns.

Revision ID: 0175
Revises: 0174
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0175"
down_revision = "0174"
branch_labels = None
depends_on = None

TABLE = "review_requests"
SYNTHETIC_DOMAIN = "qa-lead.testlookup.local"

# Frozen copies of the vocabularies in app.models.postgres. A migration must not
# import application code; tests/test_migration_0175_review_requests.py holds
# the two in step.
KINDS = ("report", "eval_drift")
SUBJECT_TYPES = ("pipeline_run", "invocation", "decision_report", "summary", "capability")
STATES = ("pending_review", "accepted", "rejected", "superseded")
REASON_CODES = (
    "wrong_category",
    "unsupported_claim",
    "missing_evidence",
    "contradiction",
    "stale_data",
    "other",
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="report"),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column(
            "pipeline_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "test_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workflow_type", sa.String(length=20), nullable=True),
        sa.Column("capability_id", sa.String(length=160), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="pending_review"),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=40), nullable=False, server_default="system"),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("evidence_bundle_sha256", sa.String(length=64), nullable=True),
        sa.Column("ai_disclaimer_version", sa.String(length=40), nullable=False),
        sa.Column(
            "superseded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{TABLE}.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_review_requests_kind"),
        sa.CheckConstraint(
            f"subject_type IN ({_in(SUBJECT_TYPES)})", name="ck_review_requests_subject_type"
        ),
        sa.CheckConstraint(f"state IN ({_in(STATES)})", name="ck_review_requests_state"),
        sa.CheckConstraint(
            f"reason_code IS NULL OR reason_code IN ({_in(REASON_CODES)})",
            name="ck_review_requests_reason_code",
        ),
        sa.CheckConstraint(
            "state <> 'rejected' OR reason_code IS NOT NULL",
            name="ck_review_requests_rejection_has_reason",
        ),
        # reviewed_at, not reviewed_by: reviewed_by is SET NULL when the user is
        # deleted, and that must not make a settled review violate a CHECK.
        sa.CheckConstraint(
            "state NOT IN ('accepted', 'rejected') OR reviewed_at IS NOT NULL",
            name="ck_review_requests_settled_has_time",
        ),
        sa.CheckConstraint(
            "evidence_bundle_sha256 IS NULL OR evidence_bundle_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_review_requests_evidence_hash",
        ),
    )
    op.create_index(
        "ix_review_requests_project_state", TABLE, ["project_id", "state", "created_at"]
    )
    op.create_index(
        "uq_review_requests_live_subject",
        TABLE,
        ["kind", "subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'superseded'"),
    )
    op.create_index(
        "ix_review_requests_pending_scope",
        TABLE,
        ["project_id", "test_run_id", "workflow_type"],
        postgresql_where=sa.text("state = 'pending_review'"),
    )

    op.add_column(
        "users",
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        f"UPDATE users SET is_synthetic = true WHERE lower(email) LIKE '%@{SYNTHETIC_DOMAIN}'"
    )
    op.add_column(
        "projects",
        sa.Column(
            "allow_unreviewed_distribution",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "allow_unreviewed_distribution")
    op.drop_column("users", "is_synthetic")
    op.drop_table(TABLE)
