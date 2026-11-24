"""Who proposed a flaky-test quarantine (product finding D-53).

* ``flaky_quarantine_requests.proposed_by_user_id`` -- nullable UUID, FK
  ``users.id`` ON DELETE SET NULL (the ``approved_by_user_id`` /
  ``rejected_by_user_id`` / ``owner_user_id`` twin). NULL means the detectors
  proposed it (or it was proposed before this revision). Adding a nullable
  column with no default is metadata-only on PostgreSQL; the FK is checked
  against existing rows, which are all NULL, on a small table.

The proposer was written only to the audit log, so the Quarantine page's
Proposed tab could not say who asked for a quarantine. ``propose_quarantine``
now stores its actor here and the list serialises ``proposed_by_name``.

No backfill: the audit log is the only earlier record. A manual proposal made
before this revision has a NULL proposer and ``detection_method = 'manual'``;
the page reads that as "Proposed manually", never "Detected automatically".

The downgrade drops the column (and its FK with it).

Revision ID: 0260
Revises: 0259
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0260"
down_revision = "0259"
branch_labels = None
depends_on = None

TABLE = "flaky_quarantine_requests"
COLUMN = "proposed_by_user_id"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            COLUMN,
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
