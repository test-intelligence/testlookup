"""Project classification hints: ``classification_hints`` (W07 FAIL-10).

A QA lead's rule of the form "<field> contains <text> -> <category>", applied
to a project's failing tests before any classification engine runs (a human
correction of the same test still wins). The table:

* ``match_field`` -- one of error_message / test_name / suite_name / class_name;
* ``pattern`` -- a case-insensitive substring (never a user regex), 3-200 chars
  (the length rule lives in ``classification_hint_service``);
* ``category`` -- a ``FailureCategory`` other than UNKNOWN;
* ``note``, ``enabled``, ``created_by`` (``ON DELETE SET NULL``: deleting a user
  keeps the hint), ``created_at`` / ``updated_at``.

Both vocabularies are CHECK constraints over FROZEN copies of the service's
tuples (``tests/test_migration_0216_classification_hints.py`` holds the three
copies equal). ``ix_classification_hints_project_enabled`` serves "the enabled
hints of this project" (one statement per analysis pipeline);
``uq_classification_hints_project_rule`` refuses a second hint with the same
field and text in any letter case. Both indexes are built on the table this
migration creates, so a plain build is allowed (the 0166+ rule concerns
existing tables).

The downgrade drops both indexes and the table.

Revision ID: 0216
Revises: 0215
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0216"
down_revision = "0215"
branch_labels = None
depends_on = None

TABLE = "classification_hints"
# FROZEN copies of classification_hint_service.MATCH_FIELDS / HINT_CATEGORIES.
_MATCH_FIELDS = ("error_message", "test_name", "suite_name", "class_name")
_CATEGORIES = ("PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY")
INDEX_ENABLED = "ix_classification_hints_project_enabled"
INDEX_RULE = "uq_classification_hints_project_rule"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_field", sa.String(20), nullable=False),
        sa.Column("pattern", sa.String(200), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            f"match_field IN ({_in(_MATCH_FIELDS)})", name="ck_classification_hints_match_field"
        ),
        sa.CheckConstraint(f"category IN ({_in(_CATEGORIES)})", name="ck_classification_hints_category"),
    )
    # A table this migration created: nothing writes it yet, a plain build is allowed.
    op.create_index(INDEX_ENABLED, TABLE, ["project_id", "enabled"])
    op.create_index(
        INDEX_RULE, TABLE, ["project_id", "match_field", sa.text("lower(pattern)")], unique=True
    )


def downgrade() -> None:
    op.drop_index(INDEX_RULE, table_name=TABLE)
    op.drop_index(INDEX_ENABLED, table_name=TABLE)
    op.drop_table(TABLE)
