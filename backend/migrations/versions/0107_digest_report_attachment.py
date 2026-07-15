"""digest report attachment opt-in

Revision ID: 0107
Revises: 0106
Create Date: 2026-07-10

PMF backlog US-7.5 — attached self-contained HTML analysis report for
daily/weekly digests.

* ``digest_subscriptions`` gains ``report_attachment`` (default FALSE =
  current behaviour: digests carry no attachment). When TRUE and the
  channel is email, the dispatcher builds the per-project analysis report
  (1d for DAILY, 7d for WEEKLY) and attaches it to the digest email.

Weekly cadence needs NO new schedule field: ``DigestSubscription.schedule``
already supports ``WEEKLY`` (dispatcher advances ``next_delivery_at`` by
one week), so the weekly report window rides the existing mechanism.
"""
from alembic import op
import sqlalchemy as sa

revision = "0107"
down_revision = "0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "digest_subscriptions",
        sa.Column(
            "report_attachment", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("digest_subscriptions", "report_attachment")
