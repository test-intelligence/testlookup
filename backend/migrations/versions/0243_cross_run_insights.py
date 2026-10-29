"""Cross-run insights: the episode table, the detector states and the flag (W23a FAIL-11).

* ``project_insights``: one row per EPISODE of one detector firing on one
  subject. It opens the first time an evaluation fires, is confirmed by every
  later evaluation that still fires (``last_seen_at`` / ``severity`` /
  ``evidence`` refreshed, ``detection_count`` + 1) and resolves once
  (``cleared`` or ``stale``); a resolved row is never reopened. ``kind`` /
  ``severity`` / ``resolved_reason`` are plain strings with CHECKs over the
  same literals as ``app.services.insight_vocab`` (pinned by
  ``tests/test_migrations_0243_0244.py`` and the vocabulary parity test).
  ``dismissed_by_name`` is a snapshot, so a dismissal still says who after the
  user is gone (``dismissed_by_id`` ``SET NULL``). Deleting the project
  deletes its insights (``CASCADE``). No FK targets ``test_runs``: evidence
  carries run ids as values.
* ``uq_project_insights_open`` -- partial unique on the OPEN rows
  (``resolved_at IS NULL``) -- is the upsert identity; a resolved episode does
  not block the next one. ``ix_project_insights_project_last_seen`` serves the
  list (``last_seen_at`` in the range).
* ``insight_detector_states``: the last evaluation of each detector for each
  project (``measured`` + a ``reason_code``), so "not measured" is shown and
  never read as health.
* The flag ``cross_run_insights`` (scope: project) seeded ON on EVERY install,
  new or upgraded (a NEW key: no administrator has chosen anything yet, so the
  fresh-install condition of 0201 / 0204 / 0229 has nothing to respect). A row
  that already exists (an admin created the key by hand) is left exactly as it
  is (``ON CONFLICT (key) DO NOTHING``).

Both tables are NEW, so their indexes are built in the same transaction,
non-concurrently (the 0166+ rule exempts a table the migration created
itself, ``tests/regression/test_migration_index_builds_are_concurrent.py``).
No backfill.

The downgrade deletes the flag row only while it still equals the seed (the
0209 / 0229 rule: same description, never edited, no project / role lists,
rollout 100; an edited row is kept), then drops the state table, the two
indexes and the insight table.

Revision ID: 0243
Revises: 0242
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0243"
down_revision = "0242"
branch_labels = None
depends_on = None

_KINDS = ("perf_drift", "flake_rising", "coverage_drop", "cost_budget")       # == insight_vocab.INSIGHT_KINDS (parity test)
_SEVERITIES = ("low", "medium", "high")                                        # == insight_vocab.INSIGHT_SEVERITIES
_REASONS = ("cleared", "stale")                                                # == insight_vocab.INSIGHT_RESOLVE_REASONS

#: (key, description). Named ``_FLAGS`` so
#: tests/regression/test_feature_flag_catalogue_is_complete.py reads the key.
_FLAGS = (
    (
        "cross_run_insights",
        "Cross-run insights on Overview and in the Inbox: per-test and per-suite slowdowns, rising flakiness, "
        "a coverage drop and the AI budget, from rules over stored results (FAIL-11; no LLM).",
    ),
)

INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "VALUES (gen_random_uuid(), :key, :description, true, 100, now(), now()) "
    "ON CONFLICT (key) DO NOTHING"
)

DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "project_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject_key", sa.String(600), nullable=False),
        sa.Column("subject_label", sa.String(1000), nullable=False),
        sa.Column("window_key", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detection_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_reason", sa.String(10), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dismissed_by_name", sa.String(255), nullable=True),
        sa.CheckConstraint(f"kind IN ({_in(_KINDS)})", name="ck_project_insights_kind"),
        sa.CheckConstraint(f"severity IN ({_in(_SEVERITIES)})", name="ck_project_insights_severity"),
        sa.CheckConstraint(
            f"resolved_reason IS NULL OR resolved_reason IN ({_in(_REASONS)})",
            name="ck_project_insights_resolved_reason",
        ),
        sa.CheckConstraint("(resolved_at IS NULL) = (resolved_reason IS NULL)", name="ck_project_insights_resolved_pair"),
    )
    op.create_index(
        "uq_project_insights_open",
        "project_insights",
        ["project_id", "kind", "subject_key", "window_key"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index("ix_project_insights_project_last_seen", "project_insights", ["project_id", "last_seen_at"])
    op.create_table(
        "insight_detector_states",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("measured", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=True),
        sa.Column("subjects_measured", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("firing", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(f"kind IN ({_in(_KINDS)})", name="ck_insight_detector_states_kind"),
    )
    for key, description in _FLAGS:
        op.execute(sa.text(INSERT_SQL).bindparams(key=key, description=description))


def downgrade() -> None:
    for key, description in _FLAGS:
        op.execute(sa.text(DELETE_SQL).bindparams(key=key, description=description))
    op.drop_table("insight_detector_states")
    op.drop_index("uq_project_insights_open", table_name="project_insights")
    op.drop_index("ix_project_insights_project_last_seen", table_name="project_insights")
    op.drop_table("project_insights")
