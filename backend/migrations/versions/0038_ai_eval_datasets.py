"""Add ai_eval_datasets and ai_eval_runs tables (OPS-02).

Revision ID: 0038
Revises: 0037
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_eval_datasets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("item_count", sa.Integer(), server_default="0"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_aed_task_type", "ai_eval_datasets", ["task_type"])

    op.create_table(
        "ai_eval_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", UUID(as_uuid=True), sa.ForeignKey("ai_eval_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_version_id", UUID(as_uuid=True), sa.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("precision", sa.Float(), nullable=True),
        sa.Column("recall", sa.Float(), nullable=True),
        sa.Column("f1_score", sa.Float(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("agreement_rate", sa.Float(), nullable=True),
        sa.Column("item_results", sa.JSON(), nullable=True),
        sa.Column("total_items", sa.Integer(), server_default="0"),
        sa.Column("correct_items", sa.Integer(), server_default="0"),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_aer_dataset", "ai_eval_runs", ["dataset_id"])
    op.create_index("ix_aer_time", "ai_eval_runs", ["evaluated_at"])


def downgrade() -> None:
    op.drop_table("ai_eval_runs")
    op.drop_table("ai_eval_datasets")
