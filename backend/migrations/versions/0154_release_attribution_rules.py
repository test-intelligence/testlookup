"""S3a — per-project rules for attributing a run to a release.

Why rules exist at all
----------------------
The attribution ladder's strongest rung is an explicit ``release_name`` from
the client. Plenty of teams cannot supply one: the CI job predates the field, or
it is owned by another team, or the release lives in Jira where nothing connects
it to a build. For those projects the ladder would fall straight to the active
release — correct often enough to be useless, because it cannot separate a
hotfix branch from a release candidate from trunk CI.

Rules are the bridge. They match on what a run already carries — branch, build
label, environment, tag — and name the release it belongs to. A project that
cuts release branches gets accurate attribution without touching a single
pipeline.

Why a table rather than config
------------------------------
Attribution decides what a GO/NO-GO verdict is computed from, so it needs the
same treatment as anything else that does: per-project, versioned by
``updated_at``, editable by the people who own the project, and auditable. A
settings blob would be none of those.

Indexes are inline, deliberately
--------------------------------
0153 exists because ``CREATE INDEX CONCURRENTLY`` cannot run in a transaction,
and mixing one into a column-adding migration makes that migration
non-re-runnable. That reasoning does not apply here: this migration CREATES the
table, so the index is built on zero rows and takes no meaningful lock. Building
it inline keeps the whole migration atomic — table and index arrive together or
neither does, which is what you want for a new object.
"""

import sqlalchemy as sa
from alembic import op

revision = "0154"
down_revision = "0153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_attribution_rules",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Lower runs first. Not unique: two rules may share a priority, and the
        # evaluator breaks that tie on (created_at, id) so the outcome is
        # deterministic rather than dependent on row order. A unique constraint
        # would instead make reordering a multi-statement dance.
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        # What to look at on the run: branch | build_number | environment | tag.
        sa.Column("match_field", sa.String(length=30), nullable=False),
        # Glob for branch/build_number ("release/*", "nightly-*"); exact,
        # case-insensitive, for environment/tag. Glob rather than regex on
        # purpose: this is user-authored, and a regex is both easy to get
        # subtly wrong and a denial-of-service surface on untrusted input.
        sa.Column("match_pattern", sa.String(length=255), nullable=False),
        # The release this rule attributes to, BY NAME rather than by id.
        # A rule usually exists before the release does — "release/2.5.*" is
        # written while 2.5.0 is still hypothetical — and a name resolves
        # through the same auto-create path every other rung uses. An id would
        # force the release to exist first and would dangle if it were deleted.
        sa.Column("target_release_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_by_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # The only read this table has: every enabled rule for one project, in
    # evaluation order, on every unlabelled ingest.
    op.create_index(
        "ix_attribution_rules_project_priority",
        "release_attribution_rules",
        ["project_id", "priority"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attribution_rules_project_priority",
        table_name="release_attribution_rules",
    )
    op.drop_table("release_attribution_rules")
