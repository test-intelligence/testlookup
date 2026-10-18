"""Test-case editing: the review setting, the approval marker, version source and owner, the executed version (W28).

Five nullable-or-defaulted columns, no backfill, no index:

* ``projects.require_case_review`` (TCM-02): a test case needs an approved
  review before it can be made Active. ``NOT NULL DEFAULT false``, so every
  existing project reads false (owner decision OR-04: review is off until a
  QA lead turns it on per project).
* ``managed_test_cases.edited_since_approval_version`` (TCM-01): the version
  at which a steps / expected-result / preconditions edit was saved while the
  project required review and the case was approved or active. NULL = not
  edited since its approval; the lifecycle clears it on an approval or when
  the case leaves Approved / Active.
* ``test_case_versions.source`` (TCM-04): ``person``, ``ai`` or ``api``, the
  kind of writer behind a version. NULL = not recorded (every row written
  before this revision).
* ``test_case_versions.assignee_id`` (TCM-04): the case's owner at that
  version. ``ON DELETE SET NULL`` like every other user reference there.
* ``test_plan_items.executed_case_version`` (TCM-05): the case version the
  recorded execution ran against. NULL = not run, or run before this revision.

Nothing filters on these columns, so no index is built (and the concurrent
index guard has nothing to see). The vocabularies are judged in the services,
so there is no CHECK constraint.

The downgrade drops the five columns in reverse order; the foreign key goes
with its column.

Revision ID: 0222
Revises: 0221
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0222"
down_revision = "0221"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "require_case_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("edited_since_approval_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "test_case_versions",
        sa.Column("source", sa.String(16), nullable=True),
    )
    op.add_column(
        "test_case_versions",
        sa.Column(
            "assignee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_tcv_assignee_id_users"),
            nullable=True,
        ),
    )
    op.add_column(
        "test_plan_items",
        sa.Column("executed_case_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("test_plan_items", "executed_case_version")
    op.drop_column("test_case_versions", "assignee_id")
    op.drop_column("test_case_versions", "source")
    op.drop_column("managed_test_cases", "edited_since_approval_version")
    op.drop_column("projects", "require_case_review")
