"""Replace 0169's first "Run #N" index with the bounded one (review R-B45-D-1).

0169 first shipped as ``ix_test_runs_project_suite_natural_seq``: the
normalised suite NAME as a key column plus ``INCLUDE (primary_suite_name)``.
``primary_suite_name`` is a String(500), so a multibyte name of legal length
made an index row past btree's 2,704-byte limit (500 CJK characters: 3,088
bytes; 500 emoji: 4,096). Every ingest of such a run failed on INSERT.

0169 now builds ``ix_test_runs_project_suite_hash_seq`` on the md5 of the
normalised name. A database that ran the first 0169 still has the old index,
so this revision:

* drops ``ix_test_runs_project_suite_natural_seq`` CONCURRENTLY, if present;
* builds the bounded index CONCURRENTLY if it is missing (dropping an INVALID
  leftover of its name first).

On a database that ran the current 0169 both steps are no-ops.

Downgrade is a no-op on purpose: the old index is the defect, and 0169's own
downgrade drops the bounded one.

Revision ID: 0172
Revises: 0171
"""
import importlib.util
import pathlib

from alembic import op

revision = "0172"
down_revision = "0171"
branch_labels = None
depends_on = None

OLD_INDEX = "ix_test_runs_project_suite_natural_seq"


def _m0169():
    path = pathlib.Path(__file__).with_name("0169_test_runs_suite_seq_index.py")
    spec = importlib.util.spec_from_file_location("_m0169_for_0172", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade() -> None:
    m0169 = _m0169()
    stale = m0169.INDEX if op.get_context().as_sql else m0169._invalid_leftover(op.get_bind())
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {OLD_INDEX}")
        if stale:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {stale}")
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {m0169.INDEX} ON {m0169.TABLE} {m0169.DEFINITION}"
        )


def downgrade() -> None:
    # Nothing to undo: the old index must not come back, and 0169's downgrade
    # drops the bounded one.
    pass
