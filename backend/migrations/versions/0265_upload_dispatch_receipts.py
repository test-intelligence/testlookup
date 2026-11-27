"""Persist upload identity before object storage and broker publication.

Revision ID: 0265
Revises: 0264
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0265"
down_revision = "0264"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_dispatch_receipts",
        sa.Column("task_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_digest", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("idempotency_digest", sa.String(64)),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("task_kwargs", JSONB, nullable=False),
        sa.Column("state", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("status_payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload_ready_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("payload_deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "actor_digest", "kind", "idempotency_digest", name="uq_upload_receipt_idempotency"),
        sa.CheckConstraint("kind IN ('batch','file')", name="ck_upload_receipt_kind"),
        sa.CheckConstraint("state IN ('pending','parsing','ingesting','succeeded','failed')", name="ck_upload_receipt_state"),
        sa.CheckConstraint("(state IN ('succeeded','failed')) = (completed_at IS NOT NULL)", name="ck_upload_receipt_terminal"),
        sa.CheckConstraint("payload_deleted_at IS NULL OR completed_at IS NOT NULL", name="ck_upload_receipt_cleanup_terminal"),
    )
    op.create_index("ix_upload_receipt_cleanup", "upload_dispatch_receipts", [sa.text("payload_deleted_at ASC NULLS FIRST"), "completed_at"], postgresql_where=sa.text("completed_at IS NOT NULL"))
    op.create_index("ix_upload_receipt_project_created", "upload_dispatch_receipts", ["project_id", "created_at"])
    op.create_index("ix_upload_receipt_outstanding", "upload_dispatch_receipts", ["project_id"], postgresql_where=sa.text("payload_deleted_at IS NULL"))


def downgrade() -> None:
    op.execute("""DO $upload_receipts$ BEGIN
        IF EXISTS (SELECT 1 FROM upload_dispatch_receipts
                   WHERE completed_at IS NULL OR payload_deleted_at IS NULL) THEN
            RAISE EXCEPTION 'Resolve uploads and remove their queued payloads before removing receipts';
        END IF;
    END $upload_receipts$;""")
    op.drop_table("upload_dispatch_receipts")
