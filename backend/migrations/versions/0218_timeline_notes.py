"""Timeline notes pinned on a project's daily charts: ``timeline_notes`` (W08 TRND-04).

A note is an inclusive UTC day range of one project (``starts_on`` ..
``ends_on``; a one-day note has both equal) with up to 280 characters of text
and, optionally, one suite (``suite_name``; NULL = every suite of the project).
The Trends hero chart and the Coverage history chart draw them.

* ``created_by_id`` -- ``ON DELETE SET NULL``: deleting a user keeps the note;
* ``ck_timeline_notes_range`` -- a note ends on or after the day it starts;
* ``ck_timeline_notes_text`` -- 1 to 280 characters (the service strips the
  text first; ``timeline_note_service.NOTE_TEXT_MAX`` and the schemas' 280
  are held equal by ``tests/test_timeline_notes.py``);
* ``ix_timeline_notes_project_starts`` -- the window read (``project_id`` and
  ``starts_on <= :to``). Built on the table this migration creates, so a plain
  build is allowed (the 0166+ rule concerns existing tables).

The project's ``ON DELETE CASCADE`` removes its notes with it.

The downgrade drops the index and the table.

Revision ID: 0218
Revises: 0217
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0218"
down_revision = "0217"
branch_labels = None
depends_on = None

TABLE = "timeline_notes"
INDEX = "ix_timeline_notes_project_starts"
#: The CHECK texts, equal to the ORM's (``tests/test_migration_0218_timeline_notes.py``).
RANGE_CHECK = "ends_on >= starts_on"
TEXT_CHECK = "char_length(text) BETWEEN 1 AND 280"


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
        sa.Column("suite_name", sa.String(500), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("text", sa.String(280), nullable=False),
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(RANGE_CHECK, name="ck_timeline_notes_range"),
        sa.CheckConstraint(TEXT_CHECK, name="ck_timeline_notes_text"),
    )
    # A table this migration created: nothing writes it yet, a plain build is allowed.
    op.create_index(INDEX, TABLE, ["project_id", "starts_on"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
