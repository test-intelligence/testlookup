"""Fold the retired "Flaky test detected" event into "Test newly flaky" (W09 NTF-01).

``flaky_test_detected`` never had a producer: a preference holding it received
nothing. ``test.newly_flaky`` fires when a test enters the known-flaky set
(``notification_transitions``), so a person who ticked "Flaky test detected"
meant it. The API now stores the alias as ``test.newly_flaky``
(``schemas.normalise_notification_events``); this migration rewrites the rows
already stored.

Upgrade:

1. ``notification_pref_rewrites_0220`` -- a bookkeeping table owned by this
   migration and read only by its downgrade (no ORM class): one row per
   preference it rewrites, with ``had_alias_target`` = the preference already
   held ``test.newly_flaky`` too. Its primary key is the preference id
   (``ON DELETE CASCADE``); it is a table this migration creates, so a plain
   build is allowed.
2. Every preference whose ``events`` is a JSON array holding the alias is
   recorded there.
3. The rewrite: each ``"flaky_test_detected"`` element becomes
   ``"test.newly_flaky"``, order kept, duplicates dropped (first occurrence
   wins). NULL, non-array and alias-free rows are untouched; a second run finds
   nothing to rewrite.

Downgrade: for each recorded preference that does not hold the alias again,
``had_alias_target`` -> the alias is appended (both were held before the
upgrade); otherwise every ``"test.newly_flaky"`` element is replaced by the
alias, order kept (a person who un-ticked "Test newly flaky" after the upgrade
keeps it off: there is nothing to replace). Then the table is dropped.
Membership is restored exactly; the POSITION of an appended alias may differ
from where it stood before the upgrade (delivery reads membership only,
``manager._build_notification_plans``).

``ALIAS_FROM`` / ``ALIAS_TO`` repeat ``schemas.LEGACY_EVENT_ALIASES`` (a
migration imports no app code); ``tests/test_migration_0220_flaky_preference_rewrite.py``
holds the two equal.

Revision ID: 0220
Revises: 0219
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0220"
down_revision = "0219"
branch_labels = None
depends_on = None

ALIAS_FROM = "flaky_test_detected"
ALIAS_TO = "test.newly_flaky"
MARKER_TABLE = "notification_pref_rewrites_0220"

#: A preference this migration rewrites: its events are an array holding the alias.
HOLDS_ALIAS = (
    "jsonb_typeof(events) = 'array' AND events @> '[\"flaky_test_detected\"]'::jsonb"
)

RECORD_SQL = (
    f"INSERT INTO {MARKER_TABLE} (preference_id, had_alias_target) "
    "SELECT id, events @> '[\"test.newly_flaky\"]'::jsonb FROM notification_preferences "
    f"WHERE {HOLDS_ALIAS} ON CONFLICT DO NOTHING"
)

REWRITE_SQL = (
    "UPDATE notification_preferences p SET events = ("
    "SELECT COALESCE(jsonb_agg(v ORDER BY first_pos), '[]'::jsonb) FROM ("
    "SELECT v, MIN(pos) AS first_pos FROM ("
    "SELECT CASE WHEN e.value = '\"flaky_test_detected\"'::jsonb "
    "THEN '\"test.newly_flaky\"'::jsonb ELSE e.value END AS v, e.pos "
    "FROM jsonb_array_elements(p.events) WITH ORDINALITY AS e(value, pos)"
    ") mapped GROUP BY v"
    ") dedup"
    ") WHERE jsonb_typeof(p.events) = 'array' AND p.events @> '[\"flaky_test_detected\"]'::jsonb"
)

#: Both were held before the upgrade: append the alias back (its position may differ).
RESTORE_BOTH_SQL = (
    "UPDATE notification_preferences p SET events = p.events || '[\"flaky_test_detected\"]'::jsonb "
    f"FROM {MARKER_TABLE} m WHERE m.preference_id = p.id AND m.had_alias_target "
    "AND jsonb_typeof(p.events) = 'array' "
    "AND NOT p.events @> '[\"flaky_test_detected\"]'::jsonb"
)

#: Only the alias was held: every "test.newly_flaky" element goes back to the alias, order kept.
RESTORE_ALIAS_SQL = (
    "UPDATE notification_preferences p SET events = ("
    "SELECT COALESCE(jsonb_agg(CASE WHEN e.value = '\"test.newly_flaky\"'::jsonb "
    "THEN '\"flaky_test_detected\"'::jsonb ELSE e.value END ORDER BY e.pos), '[]'::jsonb) "
    "FROM jsonb_array_elements(p.events) WITH ORDINALITY AS e(value, pos)"
    f") FROM {MARKER_TABLE} m WHERE m.preference_id = p.id AND NOT m.had_alias_target "
    "AND jsonb_typeof(p.events) = 'array' "
    "AND NOT p.events @> '[\"flaky_test_detected\"]'::jsonb"
)


def upgrade() -> None:
    # A table this migration creates (a primary key only): a plain build is allowed.
    op.create_table(
        MARKER_TABLE,
        sa.Column(
            "preference_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_preferences.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("had_alias_target", sa.Boolean(), nullable=False),
    )
    op.execute(sa.text(RECORD_SQL))
    op.execute(sa.text(REWRITE_SQL))


def downgrade() -> None:
    op.execute(sa.text(RESTORE_BOTH_SQL))
    op.execute(sa.text(RESTORE_ALIAS_SQL))
    op.drop_table(MARKER_TABLE)
