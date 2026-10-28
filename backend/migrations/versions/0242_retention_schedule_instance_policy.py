"""The purge window, the pause, per-field inheritance and the instance policy (W22b ACT-06).

* ``project_retention_policies``: the six policy columns (``_INHERIT``) lose
  NOT NULL and their server defaults -- ``ALTER COLUMN ... DROP NOT NULL`` /
  ``DROP DEFAULT`` change the catalog only. NO row is rewritten: every
  existing explicit value (a row written by ``upsert_policy`` or one filled by
  the old server defaults) reads exactly as before. From now on a NULL means
  "inherit": the effective value is the project column, else the instance
  column, else the code default, per field.
* ``purge_window_start_utc`` (SMALLINT, NULL = inherit, CHECK 0-23) -- the UTC
  hour the hourly beat purges the project in -- and ``purge_paused_until``
  (TIMESTAMPTZ, project-only).
* ``instance_retention_policy``: ONE row (``id = 1``, CHECK) holding the
  instance defaults. Its ``enabled`` column defaults to ``sa.false()`` and the
  inserted row takes that default (the owner's D12: the instance policy ships
  off; switching it on is a previewed, typed, audited admin action). Every
  other value is NULL (= the code default).

No index anywhere (nothing for the concurrent-build rule).

Downgrade, the exact inverse, which never switches retention on: freeze every
NULL into the value the project was resolving to (the instance value, else
the code default; the on/off flag is frozen with ``COALESCE(p.enabled,
false)``, so a project inheriting an enabled instance default comes back OFF
-- a schema rollback never starts deleting data), drop the instance table, the
window CHECK and the two new columns, then restore NOT NULL and the old
server defaults on the six columns.

Revision ID: 0242
Revises: 0241
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0242"
down_revision = "0241"
branch_labels = None
depends_on = None

_INHERIT = ("enabled", "raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs")
_CODE_DEFAULTS = {"raw_events_days": 90, "runs_days": 365, "artifacts_days": 180, "audit_days": 2555, "keep_last_runs": 10}
_WINDOW_CHECK = "purge_window_start_utc IS NULL OR purge_window_start_utc BETWEEN 0 AND 23"


def upgrade() -> None:
    for col in _INHERIT:
        op.alter_column("project_retention_policies", col, nullable=True, server_default=None)
    op.add_column("project_retention_policies", sa.Column("purge_window_start_utc", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("ck_retention_purge_window", "project_retention_policies", _WINDOW_CHECK)
    op.add_column("project_retention_policies", sa.Column("purge_paused_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "instance_retention_policy",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        *(
            sa.Column(c, sa.Integer(), nullable=True)
            for c in ("raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs")
        ),
        sa.Column("purge_window_start_utc", sa.SmallInteger(), nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enabled_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("id = 1", name="ck_instance_retention_single_row"),
        sa.CheckConstraint(_WINDOW_CHECK, name="ck_instance_retention_window"),
        sa.CheckConstraint(
            "keep_last_runs IS NULL OR keep_last_runs BETWEEN 0 AND 1000",
            name="ck_instance_retention_keep_last",
        ),
    )
    # The on/off column takes its server default (off) -- D12.
    op.execute("INSERT INTO instance_retention_policy (id) VALUES (1)")


def downgrade() -> None:
    # (1) Freeze every inherited (NULL) value into what the project resolved to.
    #     The on/off flag never inherits here: a NULL is frozen OFF.
    day_sets = ", ".join(
        f"{col} = COALESCE(p.{col}, i.{col}, {default})" for col, default in _CODE_DEFAULTS.items()
    )
    op.execute(
        "UPDATE project_retention_policies AS p "
        f"SET {day_sets}, enabled = COALESCE(p.enabled, false) "
        "FROM instance_retention_policy AS i WHERE i.id = 1"
    )
    # ... and the same with the code defaults alone (a database whose instance row is missing).
    code_sets = ", ".join(f"{col} = COALESCE({col}, {default})" for col, default in _CODE_DEFAULTS.items())
    op.execute(f"UPDATE project_retention_policies SET {code_sets}, enabled = COALESCE(enabled, false)")
    # (2) The instance table.
    op.drop_table("instance_retention_policy")
    # (3) The window CHECK and the two new columns.
    op.drop_constraint("ck_retention_purge_window", "project_retention_policies", type_="check")
    op.drop_column("project_retention_policies", "purge_paused_until")
    op.drop_column("project_retention_policies", "purge_window_start_utc")
    # (4) NOT NULL and the old server defaults back on the six columns.
    op.alter_column("project_retention_policies", "enabled", nullable=False, server_default=sa.text("false"))
    for col, default in _CODE_DEFAULTS.items():
        op.alter_column("project_retention_policies", col, nullable=False, server_default=sa.text(str(default)))
