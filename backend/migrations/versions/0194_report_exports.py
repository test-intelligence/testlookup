"""VIZ-607 — background report exports.

A Summary Report export above the synchronous threshold (or one the reader
asks to run in the background) is a ``report_exports`` row rendered by a
Celery task on the ``default`` queue; the file goes to object storage under
``report-exports/YYYY/MM/DD/<project>/<export>/``.

Status vocabulary is the states something can reach:
``queued|running|completed|failed``. No ``cancelled``: nothing produces it.

``expires_at`` is set when the row is created (requested + 7 days): after it
the download is 410 and the nightly sweep deletes the file and the row.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0194"
down_revision = "0193"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_exports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("report", sa.String(length=40), nullable=False),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        # NULL until the file exists: an unmeasured size is not a 0-byte file.
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_report_exports_status",
        ),
        sa.CheckConstraint("format IN ('pdf', 'xlsx')", name="ck_report_exports_format"),
    )
    op.create_index(
        "ix_report_exports_project_user_requested",
        "report_exports",
        ["project_id", "requested_by_id", "requested_at"],
    )
    op.create_index("ix_report_exports_expires_at", "report_exports", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_report_exports_expires_at", table_name="report_exports")
    op.drop_index("ix_report_exports_project_user_requested", table_name="report_exports")
    op.drop_table("report_exports")
