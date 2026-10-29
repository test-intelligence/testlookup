"""Expected run cadences and their decided windows (W23a TRND-03).

* ``run_cadences``: one EXPECTATION per row -- "a run of <suite | any run of
  the project> arrives by <local time> on <days> in <IANA zone>, with <grace>
  minutes of slack". TestLookup's own expectation: nothing here reads or
  writes CI. ``mode`` is ``weekly`` (a weekday bitmask + an ``expected_by``
  wall-clock time) or ``cron`` (a normalised ``m h * * dow`` string); CHECKs
  keep the two shapes exclusive, the grace within 0-720 minutes, the four
  pause fields together, and ``suite_name`` / ``suite_key`` together.
  ``suite_key`` is the lower + trimmed suite (NULL = any run).
  ``effective_from`` is when the current definition took effect;
  ``checked_through`` is the beat's cursor.
* ``uq_run_cadences_project_any`` -- partial unique on ``project_id`` WHERE
  ``suite_key IS NULL`` -- allows ONE "any run" cadence per project (a plain
  unique over a NULL column would allow many); ``uq_run_cadences_project_suite``
  -- partial unique on ``(project_id, suite_key)`` WHERE ``suite_key IS NOT
  NULL`` -- one cadence per suite.
* ``run_cadence_windows``: what the beat DECIDED for each expected window
  (``met`` / ``missed`` / ``paused``), unique on ``(cadence_id,
  window_start)``, so a redelivered tick decides nothing twice and a later
  resume never rewrites a ``paused`` window into a miss.
  ``ix_run_cadence_windows_project_deadline`` serves the status read.

Deleting a project deletes its cadences and windows; deleting a cadence
deletes its windows (``CASCADE``); deleting a user keeps the rows (``SET
NULL``). No FK targets ``test_runs``. No flag, no seed, no backfill: a
project has no cadence until a QA lead adds one.

Both tables are NEW, so their indexes are built in the same transaction,
non-concurrently (the 0166+ rule exempts a table the migration created
itself). The downgrade is the exact inverse.

Revision ID: 0244
Revises: 0243
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0244"
down_revision = "0243"
branch_labels = None
depends_on = None

_MODES = ("weekly", "cron")                       # == run_cadence_vocab.CADENCE_MODES
_OUTCOMES = ("met", "missed", "paused")           # == run_cadence_vocab.WINDOW_OUTCOMES


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "run_cadences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suite_name", sa.String(500), nullable=True),
        sa.Column("suite_key", sa.String(500), nullable=True),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("weekdays", sa.SmallInteger(), nullable=True),
        sa.Column("expected_by", sa.Time(), nullable=True),
        sa.Column("cron", sa.String(100), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("grace_minutes", sa.SmallInteger(), server_default=sa.text("60"), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(200), nullable=True),
        sa.Column("paused_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"mode IN ({_in(_MODES)})", name="ck_run_cadences_mode"),
        sa.CheckConstraint(
            "(mode = 'weekly' AND weekdays BETWEEN 1 AND 127 AND expected_by IS NOT NULL AND cron IS NULL) "
            "OR (mode = 'cron' AND cron IS NOT NULL AND weekdays IS NULL AND expected_by IS NULL)",
            name="ck_run_cadences_mode_fields",
        ),
        sa.CheckConstraint("grace_minutes BETWEEN 0 AND 720", name="ck_run_cadences_grace"),
        sa.CheckConstraint(
            "(paused_until IS NULL) = (paused_reason IS NULL) AND (paused_until IS NULL) = (paused_at IS NULL)",
            name="ck_run_cadences_pause_fields",
        ),
        sa.CheckConstraint("(suite_name IS NULL) = (suite_key IS NULL)", name="ck_run_cadences_suite_pair"),
    )
    op.create_index(
        "uq_run_cadences_project_any",
        "run_cadences",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("suite_key IS NULL"),
    )
    op.create_index(
        "uq_run_cadences_project_suite",
        "run_cadences",
        ["project_id", "suite_key"],
        unique=True,
        postgresql_where=sa.text("suite_key IS NOT NULL"),
    )
    op.create_table(
        "run_cadence_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cadence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run_cadences.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("run_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"outcome IN ({_in(_OUTCOMES)})", name="ck_run_cadence_windows_outcome"),
        sa.CheckConstraint("window_end > window_start", name="ck_run_cadence_windows_range"),
        sa.UniqueConstraint("cadence_id", "window_start", name="uq_run_cadence_windows_cadence_start"),
    )
    op.create_index("ix_run_cadence_windows_project_deadline", "run_cadence_windows", ["project_id", "deadline"])


def downgrade() -> None:
    op.drop_index("ix_run_cadence_windows_project_deadline", table_name="run_cadence_windows")
    op.drop_table("run_cadence_windows")
    op.drop_index("uq_run_cadences_project_suite", table_name="run_cadences")
    op.drop_index("uq_run_cadences_project_any", table_name="run_cadences")
    op.drop_table("run_cadences")
