"""At most one ACTIVE release-gate policy per scope (GATE-03).

``release_gate_policies`` had no guard against two active rows in one scope
(a project, or the system default: ``project_id IS NULL``). ``publish_policy``
demoted the others with no lock, so two admins publishing at once could both
win, and ``resolve_effective_policy`` silently picked the higher version. The
system scope also had no version guard at all: ``uq_rgp_project_version`` is
``UNIQUE (project_id, version)`` and Postgres treats NULLs as distinct, so two
system drafts could share a version number.

In ONE transaction, after ``LOCK TABLE ... SHARE ROW EXCLUSIVE`` (a publish in
flight finishes first; the table holds tens of rows, so the wait is seconds):

1. demote every active row but one per scope -- the highest version stays,
   ties broken by ``activated_at`` then ``created_at`` then ``id`` (newest
   wins), which is the row ``resolve_effective_policy`` already preferred --
   and log each demotion by id, scope and version;
2. renumber duplicate SYSTEM versions: within ``project_id IS NULL``, of the rows
   sharing a ``version`` the earliest-created keeps it and every other takes
   ``max(version) + k`` in ``(created_at, id)`` order, logged by id;
3. build three partial unique indexes: ``uq_rgp_one_active_project``,
   ``uq_rgp_one_active_system`` and ``uq_rgp_system_version``.

The demotion runs BEFORE the renumbering on purpose: renumbering first could
lift a duplicate-version row above the one the evaluator was using, and the
demotion would then keep a different policy active than the one in force.

Plain transactional index builds, not ``CONCURRENTLY``: the table is tiny, and
a failed concurrent build would leave an INVALID index outside the data fix's
transaction. Re-running the upgrade is a no-op (nothing to renumber or demote,
``IF NOT EXISTS`` on the indexes).

The downgrade drops exactly the three indexes. The data fix is ONE-WAY: the
demotions and the renumbering are not restored (the old state was the defect).

Revision ID: 0210
Revises: 0209
"""
import logging

from alembic import op
import sqlalchemy as sa

revision = "0210"
down_revision = "0209"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

LOCK_SQL = "LOCK TABLE release_gate_policies IN SHARE ROW EXCLUSIVE MODE"

#: Duplicate SYSTEM versions (the only scope without a version guard): the
#: earliest-created row of each duplicated version keeps it, the others move
#: past the scope's current maximum in ``(created_at, id)`` order.
RENUMBER_SQL = (
    "WITH ranked AS ("
    "SELECT id, created_at, row_number() OVER ("
    "PARTITION BY version ORDER BY created_at ASC NULLS LAST, id ASC) AS rn "
    "FROM release_gate_policies WHERE project_id IS NULL"
    "), moved AS ("
    "SELECT id, row_number() OVER (ORDER BY created_at ASC NULLS LAST, id ASC) AS k "
    "FROM ranked WHERE rn > 1"
    "), hi AS ("
    "SELECT coalesce(max(version), 0) AS v FROM release_gate_policies WHERE project_id IS NULL"
    ") "
    "UPDATE release_gate_policies p SET version = hi.v + moved.k, updated_at = now() "
    "FROM moved, hi WHERE p.id = moved.id "
    "RETURNING p.id, p.version"
)

#: Every active row but the one ``resolve_effective_policy`` already preferred.
DEMOTE_SQL = (
    "UPDATE release_gate_policies SET is_active = false, updated_at = now() "
    "FROM (SELECT id, row_number() OVER (PARTITION BY project_id "
    "ORDER BY version DESC, activated_at DESC NULLS LAST, created_at DESC, id DESC) rn "
    "FROM release_gate_policies WHERE is_active) r "
    "WHERE release_gate_policies.id = r.id AND r.rn > 1 "
    "RETURNING release_gate_policies.id, release_gate_policies.project_id, "
    "release_gate_policies.version"
)

#: (name, column, predicate). The ORM ``ReleaseGatePolicy.__table_args__``
#: declares the same three, with the same names and predicates.
INDEXES = (
    (
        "uq_rgp_one_active_project",
        "project_id",
        "is_active IS TRUE AND project_id IS NOT NULL",
    ),
    (
        "uq_rgp_one_active_system",
        "is_active",
        "is_active IS TRUE AND project_id IS NULL",
    ),
    (
        "uq_rgp_system_version",
        "version",
        "project_id IS NULL",
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(sa.text(LOCK_SQL))
    for policy_id, project_id, version in bind.execute(sa.text(DEMOTE_SQL)).fetchall():
        logger.warning(
            "0210 demoted duplicate active policy %s (scope %s, v%s)",
            policy_id,
            project_id or "system",
            version,
        )
    for policy_id, version in bind.execute(sa.text(RENUMBER_SQL)).fetchall():
        logger.warning(
            "0210 renumbered duplicate system policy version: %s is now v%s", policy_id, version
        )
    for name, column, predicate in INDEXES:
        op.create_index(
            name,
            "release_gate_policies",
            [column],
            unique=True,
            postgresql_where=sa.text(predicate),
            if_not_exists=True,
        )


def downgrade() -> None:
    # Indexes only: the demotions and the renumbering are not restored.
    for name, _column, _predicate in reversed(INDEXES):
        op.drop_index(name, table_name="release_gate_policies", if_exists=True)
