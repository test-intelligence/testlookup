"""defect jira linking — signature dedup + external status sync

Revision ID: 0105
Revises: 0104
Create Date: 2026-07-10

PMF backlog US-6.1 / US-6.2: one-click Jira defect creation with recurrence
dedup and status sync-back.

``defects`` gains:

* ``signature_fingerprint``     — the failure signature the defect was filed
                                  for (test fingerprint, or a stable hash of
                                  the cluster label). Dedup key: a second
                                  one-click create for the same signature
                                  links to the existing open defect instead
                                  of filing a duplicate Jira issue.
* ``recurrence_count`` /
  ``last_recurrence_at``        — bumped each time a create request dedups
                                  onto this defect ("recurred in build X"
                                  Jira comment audit trail).
* ``external_status_at``        — when the Jira status mirror
                                  (existing ``jira_status`` column) was last
                                  refreshed by the sync-back beat.
* ``external_status_conflict``  — Jira says the issue is Done-category but
                                  the same signature still produced failures
                                  recently → "closed in Jira but still
                                  failing" warning badge.

Composite index ``(project_id, signature_fingerprint)`` keeps the dedup
lookup (fired on every one-click create) an index scan; partial on
non-NULL signatures so legacy rows don't bloat it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0105"
down_revision = "0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "defects",
        sa.Column("signature_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column(
            "recurrence_count", sa.Integer(), nullable=False, server_default="0",
        ),
    )
    op.add_column(
        "defects",
        sa.Column("last_recurrence_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column("external_status_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "defects",
        sa.Column(
            "external_status_conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_defects_project_signature",
        "defects",
        ["project_id", "signature_fingerprint"],
        postgresql_where=sa.text("signature_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_defects_project_signature", table_name="defects")
    op.drop_column("defects", "external_status_conflict")
    op.drop_column("defects", "external_status_at")
    op.drop_column("defects", "last_recurrence_at")
    op.drop_column("defects", "recurrence_count")
    op.drop_column("defects", "signature_fingerprint")
