"""Add a quarantine population cap.

Phase 5 (P5-B) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

The quarantine lifecycle already carried an SLA, auto-defect creation and
auto-promotion. What it had no notion of was **how big the pile is allowed to
get**.

That is the failure mode the literature keeps describing: quarantine becomes
"delay with documentation", the look-at-it-later pile accumulates, and the tests
are eventually deleted with the feature they covered rather than fixed. Fowler's
suggestion of no more than 8 at once is a rule of thumb, not a law — so this is
configurable per project, and crossing it produces a **visible warning**, never
a silent block. Refusing to quarantine a genuinely broken test would just push
the noise back into the build.

``0`` means unlimited, for teams who want the lifecycle without the ceiling.

Revision ID: 0134
Revises: 0133
"""
from alembic import op
import sqlalchemy as sa

revision = "0134"
down_revision = "0133"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quarantine_lifecycle_policies",
        sa.Column(
            "max_active_quarantined",
            sa.Integer(),
            nullable=False,
            server_default="8",
        ),
    )


def downgrade() -> None:
    op.drop_column("quarantine_lifecycle_policies", "max_active_quarantined")
