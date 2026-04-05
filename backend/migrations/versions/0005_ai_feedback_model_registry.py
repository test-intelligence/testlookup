"""ai_feedback and model_versions tables for continuous fine-tuning

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ai_feedback ──────────────────────────────────────────────────────────
    op.create_table(
        "ai_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ai_analysis.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rating", sa.String(25), nullable=False),
        sa.Column("corrected_category", sa.String(30), nullable=True),
        sa.Column("corrected_root_cause", sa.Text, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("exported", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_feedback_analysis",  "ai_feedback", ["analysis_id"])
    op.create_index("ix_ai_feedback_test_case", "ai_feedback", ["test_case_id"])
    op.create_index("ix_ai_feedback_rating",    "ai_feedback", ["rating"])
    op.create_index("ix_ai_feedback_created",   "ai_feedback", ["created_at"])
    op.create_index("ix_ai_feedback_exported",  "ai_feedback", ["exported"])

    # ── model_versions ───────────────────────────────────────────────────────
    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("track", sa.String(30), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="training"),
        sa.Column("training_examples", sa.Integer, nullable=False, server_default="0"),
        sa.Column("holdout_examples", sa.Integer, nullable=False, server_default="0"),
        sa.Column("eval_accuracy", sa.Float, nullable=True),
        sa.Column("baseline_accuracy", sa.Float, nullable=True),
        sa.Column("eval_details", sa.JSON, nullable=True),
        sa.Column("provider_job_id", sa.String(200), nullable=True),
        sa.Column("training_file_path", sa.String(1000), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_model_versions_track_status", "model_versions", ["track", "status"])


def downgrade() -> None:
    op.drop_index("ix_model_versions_track_status", "model_versions")
    op.drop_table("model_versions")

    op.drop_index("ix_ai_feedback_exported",  "ai_feedback")
    op.drop_index("ix_ai_feedback_created",   "ai_feedback")
    op.drop_index("ix_ai_feedback_rating",    "ai_feedback")
    op.drop_index("ix_ai_feedback_test_case", "ai_feedback")
    op.drop_index("ix_ai_feedback_analysis",  "ai_feedback")
    op.drop_table("ai_feedback")
