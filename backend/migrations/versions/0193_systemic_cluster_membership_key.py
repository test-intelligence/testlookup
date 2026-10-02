"""Systemic flake clusters get a membership key (VIZ-207).

``cluster_key`` is a rank (``sfc_001`` is tonight's largest cluster) and the
nightly sweep is a full delete + re-insert, so neither it nor the row id says
"this is the same cluster as yesterday". ``membership_key`` does, for an
UNCHANGED member set: the first 32 hex characters of sha256 over the member
fingerprints, sorted by code point and joined by ``\\n``
(``app.services.systemic_cluster_service.membership_key``). One test joining
or leaving makes a new key; that limit is stated in the API and the UI.

Additive and nullable: code that predates this revision ignores the column.
The index is NOT unique on purpose -- a duplicate written by a bug must not
abort the nightly ``store_clusters``, which is unique only on
``(project_id, cluster_key)``.

The backfill computes the same key in SQL from the existing members (a handful
of rows per project: one UPDATE). ``COLLATE "C"`` makes the order the code-point
order Python's ``sorted`` uses, whatever the database collation; a cluster with
no member row keeps NULL.

The index is built and dropped CONCURRENTLY inside an autocommit block, as
0167 does (``tests/regression/test_migration_index_builds_are_concurrent.py``):
the table is small, but a plain build or drop still queues behind every open
transaction on it. A failed earlier concurrent build leaves an INVALID index
under the name, which ``IF NOT EXISTS`` would keep; it is dropped first. The
column and the backfill commit before the build.

Revision ID: 0193
Revises: 0192
"""

from alembic import op
import sqlalchemy as sa


revision = "0193"
down_revision = "0192"
branch_labels = None
depends_on = None

_TABLE = "systemic_flake_cluster"
_INDEX = "ix_systemic_cluster_project_membership"


#: One row per cluster that has members: its key, computed in SQL. Kept as a
#: constant so ``tests/integration/test_systemic_membership_key_postgres.py``
#: holds exactly this text to ``systemic_cluster_service.membership_key``.
KEY_BY_CLUSTER_SQL = """
    SELECT
        cluster_id,
        left(encode(sha256(convert_to(
            string_agg(test_fingerprint, E'\\n' ORDER BY test_fingerprint COLLATE "C"),
            'UTF8'
        )), 'hex'), 32) AS membership_key
    FROM systemic_flake_cluster_member
    GROUP BY cluster_id
"""


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("membership_key", sa.String(32), nullable=True))
    op.execute(
        f"""
        UPDATE systemic_flake_cluster c
        SET membership_key = k.membership_key
        FROM ({KEY_BY_CLUSTER_SQL}) k
        WHERE k.cluster_id = c.id
        """
    )
    # Offline (--sql) there is no catalog: drop whatever holds the name, then build.
    invalid = True if op.get_context().as_sql else _invalid_leftover(op.get_bind())
    with op.get_context().autocommit_block():
        if invalid:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX} "
            f"ON {_TABLE} (project_id, membership_key)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX}")
    op.drop_column(_TABLE, "membership_key")


def _invalid_leftover(bind) -> bool:
    """True when an index of this name exists but is INVALID (a failed build)."""
    valid = bind.execute(
        sa.text(
            "SELECT i.indisvalid FROM pg_index i "
            "WHERE i.indexrelid = to_regclass(:index)"
        ),
        {"index": _INDEX},
    ).scalar()
    return valid is False
