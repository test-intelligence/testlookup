"""add pr_comment_mode to github_integrations

Revision ID: 0102
Revises: 0101
Create Date: 2026-07-09

PMF backlog US-4.1: sticky PR summary comment. Per-project setting that
controls when TestLookup posts/updates its single marker-keyed summary
comment on the PR a run belongs to (via test_runs.pr_number + ci_repo,
migration 0101):

* ``off``           — never comment.
* ``failures_only`` — comment when the run has failures OR when a prior
                      marker comment exists (a PR that went red→green must
                      be updated to show green). Default.
* ``always``        — comment on every finalized PR run.
"""
from alembic import op
import sqlalchemy as sa

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "github_integrations",
        sa.Column(
            "pr_comment_mode",
            sa.String(20),
            nullable=False,
            server_default="failures_only",
        ),
    )


def downgrade() -> None:
    op.drop_column("github_integrations", "pr_comment_mode")
