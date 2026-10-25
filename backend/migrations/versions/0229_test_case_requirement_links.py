"""Manual requirement keys on authored test cases, and their flag (W13 TCL-06 "lite").

One new table, its checks, a unique constraint and one index, plus one
feature-flag row:

* ``test_case_requirement_links``: one row per requirement key a PERSON typed
  on an authored test case (``managed_test_cases``). ``requirement_key`` is the
  key as written; ``requirement_key_norm`` the same key through
  ``core/test_library_vocab.normalize_requirement_key`` (one pair of
  surrounding brackets / parentheses dropped, whitespace collapsed,
  upper-cased), the form every comparison uses. ``project_id`` is the case's,
  denormalised so the project's known keys are one index read. ``link_type`` is
  ``verifies`` (default), ``partially_verifies`` or ``related``. Deleting the
  project or the case deletes its links (``ON DELETE CASCADE``); deleting the
  person who added one keeps the link (``created_by_id`` ``SET NULL``). Links
  are MANUAL only: no source column, no knowledge-source key, no backfill.
* ``ck_tcrl_link_type`` (the three link types) and ``ck_tcrl_key_norm_nonempty``
  (a normalised key is never empty).
* ``uq_tcrl_case_key`` (``test_case_id, requirement_key_norm``): a case links a
  key once, whatever its spelling.
* ``ix_tcrl_project_key`` (``project_id, requirement_key_norm``) serves the
  project's coverage summary, the gaps page and the case list's
  ``requirement=`` filter. Built on the table this migration creates (empty),
  so a plain build: the concurrent-index rule is for existing tables.
* The flag ``test_case_requirement_traceability`` (scope: project): ON for a
  NEW install, OFF for an upgraded one. The row is inserted with
  ``enabled_global`` = the fresh-install condition of 0201 / 0212 (no user and
  no project exists yet -- the state ``alembic upgrade head`` runs in on a new
  install); a row that already exists is left as it is (``ON CONFLICT (key) DO
  NOTHING``). On an upgraded deployment (the homelab) an admin turns it on at
  deploy with ``PATCH /api/v1/feature-flags/test_case_requirement_traceability``
  (``{"enabled_global": true}``). Off, the requirement routes answer 404 and the
  page shows nothing of the feature.

The downgrade deletes the flag row only while it still equals the seed (the
0209 rule: same description, never edited, no project / role lists, rollout
100; an edited row is kept), then drops the index and the table.

Revision ID: 0229
Revises: 0228
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0229"
down_revision = "0228"
branch_labels = None
depends_on = None

TABLE = "test_case_requirement_links"
INDEX_NAME = "ix_tcrl_project_key"
UNIQUE_NAME = "uq_tcrl_case_key"
LINK_TYPE_CHECK = "ck_tcrl_link_type"
KEY_NORM_CHECK = "ck_tcrl_key_norm_nonempty"

#: (key, description). Named ``_FLAGS`` so the feature-flag catalogue guard
#: reads the key.
_FLAGS = (
    (
        "test_case_requirement_traceability",
        "Requirement keys on test cases, uncovered-requirement gaps and the requirement coverage stat "
        "(TCL-06 lite: no matrix, no export, no Jira).",
    ),
)

# The fresh-install condition of 0201 / 0212: no user and no project exists.
FRESH_INSTALL = (
    "NOT EXISTS (SELECT 1 FROM users) AND NOT EXISTS (SELECT 1 FROM projects)"
)

INSERT_SQL = (
    "INSERT INTO feature_flags "
    "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
    "SELECT gen_random_uuid(), :key, :description, (" + FRESH_INSTALL + "), "
    "100, now(), now() "
    "ON CONFLICT (key) DO NOTHING"
)

DELETE_SQL = (
    "DELETE FROM feature_flags WHERE key = :key AND description = :description "
    "AND created_at = updated_at AND enabled_projects IS NULL AND enabled_roles IS NULL "
    "AND rollout_percent = 100"
)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("managed_test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_key", sa.String(200), nullable=False),
        sa.Column("requirement_key_norm", sa.String(200), nullable=False),
        sa.Column("requirement_title", sa.String(500), nullable=True),
        sa.Column("link_type", sa.String(20), nullable=False, server_default=sa.text("'verifies'")),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "link_type IN ('verifies', 'partially_verifies', 'related')", name=LINK_TYPE_CHECK,
        ),
        sa.CheckConstraint("requirement_key_norm <> ''", name=KEY_NORM_CHECK),
        sa.UniqueConstraint("test_case_id", "requirement_key_norm", name=UNIQUE_NAME),
    )
    # On the table created just above (empty): a plain build is instant.
    op.create_index(INDEX_NAME, TABLE, ["project_id", "requirement_key_norm"])
    for key, description in _FLAGS:
        op.execute(sa.text(INSERT_SQL).bindparams(key=key, description=description))


def downgrade() -> None:
    for key, description in _FLAGS:
        op.execute(sa.text(DELETE_SQL).bindparams(key=key, description=description))
    op.drop_index(INDEX_NAME, table_name=TABLE)
    op.drop_table(TABLE)
