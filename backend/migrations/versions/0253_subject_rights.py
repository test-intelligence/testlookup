"""Subject-rights jobs and the erased marker on users (W31 STRAT-12: per-person export, Art. 15 / 20; erasure, Art. 17).

* ``subject_rights_jobs``: one row per personal-data export or erasure. ``kind``
  is ``export`` / ``erasure``, ``status`` ``queued`` / ``running`` /
  ``succeeded`` / ``failed`` / ``expired``, ``source`` ``self`` / ``admin`` and
  an erasure's ``phase`` the LAST phase it committed (``lockout`` /
  ``postgres`` / ``objects`` / ``redis`` / ``done``) -- CHECKed with the
  literals of ``subject_rights_vocab.JOB_KINDS`` / ``JOB_STATUSES`` /
  ``REQUEST_SOURCES`` / ``ERASURE_PHASES`` (held here as module tuples -- a
  migration imports no application code; pinned by
  ``tests/test_migration_0253.py``). ``ck_srj_erasure_is_admin``: an erasure is
  never self-service. ``subject_user_id`` is ``ON DELETE RESTRICT`` (the user
  row is never deleted -- an erasure pseudonymises it in place);
  ``requested_by_id`` is ``SET NULL``. ``storage_key`` (the export archive) is
  never returned and never logged.
* ``uq_srj_one_live_export`` / ``uq_srj_one_live_erasure`` -- partial uniques on
  ``subject_user_id`` WHERE that kind is queued / running: one live job per
  subject and kind. ``ix_srj_subject_kind`` serves the lists and the refusals,
  ``ix_srj_status_requested`` the janitor, ``ix_srj_requester`` "my exports".
* ``users.erased_at`` timestamptz NULL -- set by the erasure's Postgres phase,
  never cleared; ``ix_users_erased_at`` is partial (``erased_at IS NOT NULL``)
  and serves the read-side "Erased user" projection. Adding a NULL column is
  metadata-only; the index is on an EXISTING table, so it is built
  ``CONCURRENTLY`` inside ``autocommit_block`` (the 0166+ rule, enforced by
  ``tests/regression/test_migration_index_builds_are_concurrent.py`` -- the
  contract's "plain index" yields to that guard), dropping an INVALID leftover
  of a failed build first.

``subject_rights_jobs`` is a NEW table, so its indexes are built in the same
transaction, non-concurrently. No data migration, no backfill, no seed, no flag.

The downgrade drops exactly what the upgrade made, in reverse: the users
index (CONCURRENTLY), the users column, the table's five indexes and the
table. A downgrade after
an erasure leaves the pseudonymised users rows pseudonymised -- an erasure is
irreversible by design; only the marker column is dropped.

Revision ID: 0253
Revises: 0252
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0253"
down_revision = "0252"
branch_labels = None
depends_on = None

TABLE = "subject_rights_jobs"
_KINDS = ("export", "erasure")                                          # == subject_rights_vocab.JOB_KINDS
_STATUSES = ("queued", "running", "succeeded", "failed", "expired")     # == subject_rights_vocab.JOB_STATUSES
_SOURCES = ("self", "admin")                                            # == subject_rights_vocab.REQUEST_SOURCES
_PHASES = ("lockout", "postgres", "objects", "redis", "done")           # == subject_rights_vocab.ERASURE_PHASES
_LIVE = "status IN ('queued', 'running')"                               # == subject_rights_vocab.LIVE_STATUSES
USERS = "users"
USERS_COLUMN = "erased_at"
USERS_INDEX = "ix_users_erased_at"
_USERS_INDEX_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_erased_at "
    "ON users (erased_at) WHERE erased_at IS NOT NULL"
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _index_is_invalid(bind) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": USERS_INDEX},
        ).first()
    )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("source", sa.String(8), server_default=sa.text("'admin'"), nullable=False),
        sa.Column("phase", sa.String(10), nullable=True),
        sa.Column(
            "subject_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column(
            "requested_by_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("archive_sha256", sa.String(64), nullable=True),
        sa.Column("signature", sa.String(16), nullable=True),
        sa.Column("signing_key_id", sa.String(64), nullable=True),
        sa.Column("counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"kind IN ({_in(_KINDS)})", name="ck_srj_kind"),
        sa.CheckConstraint(f"status IN ({_in(_STATUSES)})", name="ck_srj_status"),
        sa.CheckConstraint(f"source IN ({_in(_SOURCES)})", name="ck_srj_source"),
        sa.CheckConstraint(f"phase IS NULL OR phase IN ({_in(_PHASES)})", name="ck_srj_phase"),
        sa.CheckConstraint("kind <> 'erasure' OR source = 'admin'", name="ck_srj_erasure_is_admin"),
    )
    op.create_index("ix_srj_subject_kind", TABLE, ["subject_user_id", "kind", sa.text("requested_at DESC")])
    op.create_index(
        "uq_srj_one_live_export",
        TABLE,
        ["subject_user_id"],
        unique=True,
        postgresql_where=sa.text(f"kind = 'export' AND {_LIVE}"),
    )
    op.create_index(
        "uq_srj_one_live_erasure",
        TABLE,
        ["subject_user_id"],
        unique=True,
        postgresql_where=sa.text(f"kind = 'erasure' AND {_LIVE}"),
    )
    op.create_index("ix_srj_status_requested", TABLE, ["status", "requested_at"])
    op.create_index("ix_srj_requester", TABLE, ["requested_by_id", sa.text("requested_at DESC")])

    # A nullable column with no default: a catalogue change, no table rewrite.
    op.add_column(USERS, sa.Column(USERS_COLUMN, sa.DateTime(timezone=True), nullable=True))
    # An index on an EXISTING table is built CONCURRENTLY inside autocommit_block (the 0166+ rule,
    # tests/regression/test_migration_index_builds_are_concurrent.py); an INVALID leftover of a
    # failed build is dropped first, or IF NOT EXISTS would keep it for ever.
    invalid = False if op.get_context().as_sql else _index_is_invalid(op.get_bind())
    with op.get_context().autocommit_block():
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {USERS_INDEX}")
        op.execute(_USERS_INDEX_SQL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {USERS_INDEX}")
    op.drop_column(USERS, USERS_COLUMN)
    op.drop_index("ix_srj_requester", table_name=TABLE)
    op.drop_index("ix_srj_status_requested", table_name=TABLE)
    op.drop_index("uq_srj_one_live_erasure", table_name=TABLE)
    op.drop_index("uq_srj_one_live_export", table_name=TABLE)
    op.drop_index("ix_srj_subject_kind", table_name=TABLE)
    op.drop_table(TABLE)
