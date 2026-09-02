"""S4 — drop two columns nothing has ever written or read.

``report_share_links.storage_key_pdf`` and ``storage_key_html`` were added in
0033 for a pre-rendered PDF/HTML of a shared report. Nothing renders those, and
a repo-wide search finds no production write or read of either column in the
three years since — the only reference outside the model was a test asserting
the attribute exists.

**Why this belongs in the report-lifecycle slice specifically.** S4 adds
revoked share links to the artifacts retention clock. Anyone writing that would
see two storage-key columns and reasonably conclude a share link owns objects
that need deleting — then write an object delete against a key that is always
NULL, and report objects reclaimed that never existed. Absence is not health,
and a column that only ever holds NULL is the most convincing way to claim
otherwise.

Dropping rather than wiring: wiring means building report pre-rendering, which
is a feature, not a retention concern. If that feature ever lands it can add
the columns back with a writer in the same change.
"""

from alembic import op
import sqlalchemy as sa


revision = "0149"
down_revision = "0148"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("report_share_links", "storage_key_pdf")
    op.drop_column("report_share_links", "storage_key_html")


def downgrade() -> None:
    # Nullable with no default, exactly as 0033 created them — restoring the
    # shape does not restore data, because there was never any to lose.
    op.add_column(
        "report_share_links",
        sa.Column("storage_key_html", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "report_share_links",
        sa.Column("storage_key_pdf", sa.String(length=500), nullable=True),
    )
