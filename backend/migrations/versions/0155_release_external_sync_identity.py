"""S3b — where a release came from, when it is owned elsewhere.

TestLookup is not the system of record for release *identity*. Releases live in
Jira fix versions and GitHub/GitLab milestones, and which one varies by team.
What TestLookup owns is release *quality* — attribution, criteria, policy, the
verdict. These columns record the split.

``external_id``, not name
-------------------------
Sync keys on the provider's stable id, never on the name. Renaming a milestone
from "2.5.0" to "2.5.0 (delayed)" must update the existing release, not orphan
its entire run history and mint a second one — which is exactly what a
name-keyed sync does, silently, at the moment someone tidies up a title.

``source_system`` decides who owns what
---------------------------------------
When it is not ``local``, name/version/dates/status are read-only in the
TestLookup UI: the external system is authoritative for them and an edit here
would be overwritten by the next sync without explanation. Everything about
quality stays editable regardless of origin.

Nullable, all of it
-------------------
A release created by hand or auto-created by ingest has no external identity
and never will. NULL here means "ours", which is the common case and not a gap
to be backfilled.
"""

import sqlalchemy as sa
from alembic import op

revision = "0155"
down_revision = "0154"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("releases", sa.Column("source_system", sa.String(length=20), nullable=True))
    op.add_column("releases", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.add_column("releases", sa.Column("external_url", sa.String(length=1000), nullable=True))
    op.add_column(
        "releases", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True)
    )

    # One release per (project, source, external id). Partial, because the
    # overwhelming majority of rows are local and carry NULL on both columns —
    # and in Postgres NULLs are distinct, so an unfiltered unique index would
    # permit unlimited local releases (correct) while silently doing nothing to
    # constrain the synced ones it was written for.
    #
    # Inline rather than deferred to a separate migration: this indexes columns
    # created in this same transaction, so it builds on zero non-NULL rows and
    # takes no meaningful lock. The 0153 split exists for indexes over
    # populated tables.
    op.create_index(
        "ix_releases_external_identity",
        "releases",
        ["project_id", "source_system", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_releases_external_identity", table_name="releases")
    op.drop_column("releases", "last_synced_at")
    op.drop_column("releases", "external_url")
    op.drop_column("releases", "external_id")
    op.drop_column("releases", "source_system")
