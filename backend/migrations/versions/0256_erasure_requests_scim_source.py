"""Erasure requests + the ``scim`` job source (W38: W31 F-1 self-service erasure request, F-2 SCIM erasure).

* ``subject_erasure_requests``: one row per erasure REQUEST a person files on
  their Profile (W38 D-1: a request is not a job -- ``POST /users/{id}/erasure``
  stays the only thing that starts an erasure). ``status`` ``pending`` /
  ``withdrawn`` / ``declined`` / ``accepted`` and ``decline_code`` are CHECKed
  with the literals of ``subject_rights_vocab.ERASURE_REQUEST_STATUSES`` /
  ``DECLINE_CODES`` (held here as module tuples -- a migration imports no
  application code; pinned by ``tests/test_migration_0256.py``).
  ``ck_ser_decided``: a pending request has no ``decided_at`` and a decided one
  has; ``ck_ser_declined_has_code``: exactly the declined rows carry a code (no
  free text anywhere, D-2). ``subject_user_id`` is ``ON DELETE RESTRICT`` (the
  user row is never deleted -- an erasure pseudonymises it in place);
  ``decided_by_id`` and ``erasure_job_id`` are ``SET NULL``.
* ``uq_ser_one_pending`` -- partial unique on ``subject_user_id`` WHERE
  ``status = 'pending'``: one pending request per person (D-3; the backstop of
  the route's ``FOR UPDATE``). ``ix_ser_status_requested`` serves the queue.
* ``subject_rights_jobs`` (D-11): ``ck_srj_source`` gains ``'scim'`` and
  ``ck_srj_erasure_is_admin`` reads ``kind <> 'erasure' OR source IN ('admin',
  'scim')`` -- an erasure is still never self-service. Each CHECK is dropped and
  re-created (a brief ACCESS EXCLUSIVE lock while the small table validates).

``subject_erasure_requests`` is a NEW table, so its indexes are built in the
same transaction, non-concurrently. No data migration, no backfill, no seed, no
flag.

The downgrade FAILS CLOSED while any ``subject_rights_jobs`` row has
``source = 'scim'`` (restoring 0253's two-literal CHECKs would reject it).
Otherwise it drops exactly what the upgrade made, in reverse: the two CHECKs
(restoring 0253's literals), the table's two indexes and the table.

Revision ID: 0256
Revises: 0255
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0256"
down_revision = "0255"
branch_labels = None
depends_on = None

TABLE = "subject_erasure_requests"
_STATUSES = ("pending", "withdrawn", "declined", "accepted")      # == subject_rights_vocab.ERASURE_REQUEST_STATUSES
_DECLINE_CODES = ("legal_obligation", "active_contract", "identity_unconfirmed", "other")   # == subject_rights_vocab.DECLINE_CODES
_PENDING = "status = 'pending'"
JOBS = "subject_rights_jobs"
_SOURCES = ("self", "admin", "scim")                              # == subject_rights_vocab.REQUEST_SOURCES
_ERASURE_SOURCES = ("admin", "scim")                              # an erasure is never "self"
_SOURCES_0253 = ("self", "admin")                                 # 0253's literals (the downgrade restores them)
_SOURCE_CHECK = "ck_srj_source"
_ERASURE_CHECK = "ck_srj_erasure_is_admin"
_ERASURE_IS_ADMIN_0253 = "kind <> 'erasure' OR source = 'admin'"
_SCIM_ROWS_SQL = "SELECT count(*) FROM subject_rights_jobs WHERE source = 'scim'"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subject_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decided_by_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("decline_code", sa.String(40), nullable=True),
        sa.Column(
            "erasure_job_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subject_rights_jobs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.CheckConstraint(f"status IN ({_in(_STATUSES)})", name="ck_ser_status"),
        sa.CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name="ck_ser_decided"),
        sa.CheckConstraint(
            f"decline_code IS NULL OR decline_code IN ({_in(_DECLINE_CODES)})", name="ck_ser_decline_code",
        ),
        sa.CheckConstraint("(status = 'declined') = (decline_code IS NOT NULL)", name="ck_ser_declined_has_code"),
    )
    op.create_index(
        "uq_ser_one_pending", TABLE, ["subject_user_id"], unique=True, postgresql_where=sa.text(_PENDING),
    )
    op.create_index("ix_ser_status_requested", TABLE, ["status", sa.text("requested_at DESC")])

    op.drop_constraint(_SOURCE_CHECK, JOBS, type_="check")
    op.create_check_constraint(_SOURCE_CHECK, JOBS, f"source IN ({_in(_SOURCES)})")
    op.drop_constraint(_ERASURE_CHECK, JOBS, type_="check")
    op.create_check_constraint(_ERASURE_CHECK, JOBS, f"kind <> 'erasure' OR source IN ({_in(_ERASURE_SOURCES)})")


def downgrade() -> None:
    if not op.get_context().as_sql:
        scim = op.get_bind().execute(sa.text(_SCIM_ROWS_SQL)).scalar() or 0
        if scim:
            raise RuntimeError(
                f"{scim} subject rights job(s) have source 'scim'; 0253's CHECKs would reject them -- "
                "they are the record of SCIM-started erasures, so keep 0256"
            )
    op.drop_constraint(_ERASURE_CHECK, JOBS, type_="check")
    op.create_check_constraint(_ERASURE_CHECK, JOBS, _ERASURE_IS_ADMIN_0253)
    op.drop_constraint(_SOURCE_CHECK, JOBS, type_="check")
    op.create_check_constraint(_SOURCE_CHECK, JOBS, f"source IN ({_in(_SOURCES_0253)})")
    op.drop_index("ix_ser_status_requested", table_name=TABLE)
    op.drop_index("uq_ser_one_pending", table_name=TABLE)
    op.drop_table(TABLE)
