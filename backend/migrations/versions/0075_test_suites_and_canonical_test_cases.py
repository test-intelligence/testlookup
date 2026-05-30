"""TestSuite entity + CanonicalTestCase junction.

Phase 1 of the test-case ↔ test-suite linking feature. See CLAUDE.md
"Adding New Features" for the multi-phase plan.

What this migration does:
  1. ``test_suites``: first-class suite entity (project-scoped, ``is_default``
     flag, unique partial index ensuring at most one default per project).
  2. ``canonical_test_cases``: project-scoped test identity table that
     supersedes ``suite_memberships``. Unique on (project_id, test_fingerprint).
     Every row belongs to exactly one suite; lifecycle columns (status,
     source, first/last/deleted run pointers) are merged in from
     ``suite_memberships``.
  3. ``test_cases.canonical_test_case_id``: nullable FK that turns the per-run
     ``test_cases`` row into a junction back to the catalog. ``SET NULL`` on
     canonical deletion so an existing run history survives a suite cleanup.

Backfill (run inside the upgrade so a fresh deploy and an in-place upgrade
both end with the same state):
  - One ``Default Suite ({project.name})`` row per existing project, marked
    ``is_default=true``.
  - Additional ``test_suites`` rows for every (project, suite_name) pair seen
    in ``suite_memberships`` or in ``test_cases``.
  - ``canonical_test_cases`` populated from ``suite_memberships`` first
    (preserves lifecycle audit), then from orphan ``test_cases`` (assigned to
    the project's default suite).
  - ``test_cases.canonical_test_case_id`` populated by fingerprint join.

``suite_memberships`` / ``suite_membership_events`` are intentionally NOT
dropped here. Phase 2 will migrate the ingestion writes onto the new tables
and the cleanup migration comes after that.

Revision ID: 0075
Revises: 0074
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── test_suites ──────────────────────────────────────────────
    op.create_table(
        "test_suites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "name", name="uq_test_suites_project_name"),
    )
    op.create_index("ix_test_suites_project_id", "test_suites", ["project_id"])
    # Partial unique index: at most one is_default=true row per project.
    op.create_index(
        "ix_test_suites_project_default",
        "test_suites",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_default IS TRUE"),
    )

    # ── canonical_test_cases ─────────────────────────────────────
    op.create_table(
        "canonical_test_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_suite_id", UUID(as_uuid=True), sa.ForeignKey("test_suites.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("test_fingerprint", sa.String(64), nullable=False),
        sa.Column("test_name", sa.String(1000), nullable=False),
        sa.Column("class_name", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(20), nullable=False, server_default="execution"),
        sa.Column("first_seen_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_seen_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("deleted_at_run_id", UUID(as_uuid=True), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("managed_test_case_id", UUID(as_uuid=True), sa.ForeignKey("managed_test_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("review_tag", sa.String(50), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "test_fingerprint", name="uq_canonical_test_cases_project_fp"),
    )
    op.create_index("ix_ctc_project_id", "canonical_test_cases", ["project_id"])
    op.create_index("ix_ctc_test_suite_id", "canonical_test_cases", ["test_suite_id"])
    op.create_index("ix_ctc_project_status", "canonical_test_cases", ["project_id", "status"])
    op.create_index("ix_ctc_fingerprint", "canonical_test_cases", ["test_fingerprint"])

    # ── test_cases.canonical_test_case_id ────────────────────────
    op.add_column(
        "test_cases",
        sa.Column(
            "canonical_test_case_id",
            UUID(as_uuid=True),
            sa.ForeignKey("canonical_test_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_test_cases_canonical", "test_cases", ["canonical_test_case_id"])

    # ── Backfill ─────────────────────────────────────────────────
    # 1) One default suite per project. Names are human-readable per product
    #    decision; uniqueness is enforced per project, so name collisions
    #    across projects are fine.
    op.execute(
        """
        INSERT INTO test_suites (id, project_id, name, is_default)
        SELECT gen_random_uuid(), p.id,
               'Default Suite (' || p.name || ')', true
        FROM projects p
        WHERE NOT EXISTS (
            SELECT 1 FROM test_suites ts
            WHERE ts.project_id = p.id AND ts.is_default = true
        )
        """
    )

    # 2) Materialise non-default suites from suite_memberships history.
    op.execute(
        """
        INSERT INTO test_suites (id, project_id, name, is_default)
        SELECT gen_random_uuid(), sm.project_id, sm.suite_name, false
        FROM (
            SELECT DISTINCT project_id, suite_name
            FROM suite_memberships
            WHERE suite_name IS NOT NULL AND suite_name <> ''
        ) sm
        WHERE NOT EXISTS (
            SELECT 1 FROM test_suites ts
            WHERE ts.project_id = sm.project_id AND ts.name = sm.suite_name
        )
        """
    )

    # 3) Materialise suites referenced only by raw test_cases (no membership row).
    op.execute(
        """
        INSERT INTO test_suites (id, project_id, name, is_default)
        SELECT gen_random_uuid(), pairs.project_id, pairs.suite_name, false
        FROM (
            SELECT DISTINCT tr.project_id, tc.suite_name
            FROM test_cases tc
            JOIN test_runs tr ON tr.id = tc.test_run_id
            WHERE tc.suite_name IS NOT NULL AND tc.suite_name <> ''
        ) AS pairs
        WHERE NOT EXISTS (
            SELECT 1 FROM test_suites ts
            WHERE ts.project_id = pairs.project_id AND ts.name = pairs.suite_name
        )
        """
    )

    # 4) Seed canonical_test_cases from suite_memberships. If a fingerprint
    #    appears in multiple suites within one project, pick the most-recent
    #    by updated_at (DISTINCT ON in Postgres). Lifecycle columns are
    #    preserved 1:1.
    op.execute(
        """
        INSERT INTO canonical_test_cases (
            id, project_id, test_suite_id, test_fingerprint, test_name, class_name,
            status, source, first_seen_run_id, last_seen_run_id, deleted_at_run_id,
            managed_test_case_id, review_tag
        )
        SELECT DISTINCT ON (sm.project_id, sm.test_fingerprint)
            gen_random_uuid(),
            sm.project_id,
            ts.id,
            sm.test_fingerprint,
            sm.test_name,
            sm.class_name,
            sm.status,
            sm.source,
            sm.first_seen_run_id,
            sm.last_seen_run_id,
            sm.deleted_at_run_id,
            sm.managed_test_case_id,
            sm.review_tag
        FROM suite_memberships sm
        JOIN test_suites ts
          ON ts.project_id = sm.project_id AND ts.name = sm.suite_name
        WHERE NOT EXISTS (
            SELECT 1 FROM canonical_test_cases ctc
            WHERE ctc.project_id = sm.project_id
              AND ctc.test_fingerprint = sm.test_fingerprint
        )
        ORDER BY sm.project_id, sm.test_fingerprint, sm.updated_at DESC NULLS LAST
        """
    )

    # 5) Seed canonical_test_cases for fingerprints that exist in test_cases
    #    but were never tracked in suite_memberships (typically: tests
    #    ingested with NULL/empty suite_name). Assign them to each project's
    #    default suite. Use the most-recent test_case row for name/class.
    op.execute(
        """
        INSERT INTO canonical_test_cases (
            id, project_id, test_suite_id, test_fingerprint, test_name, class_name,
            status, source, first_seen_run_id, last_seen_run_id
        )
        SELECT DISTINCT ON (tr.project_id, tc.test_fingerprint)
            gen_random_uuid(),
            tr.project_id,
            ds.id,
            tc.test_fingerprint,
            tc.test_name,
            tc.class_name,
            'active',
            'execution',
            tc.test_run_id,
            tc.test_run_id
        FROM test_cases tc
        JOIN test_runs tr ON tr.id = tc.test_run_id
        JOIN test_suites ds
          ON ds.project_id = tr.project_id AND ds.is_default = true
        WHERE NOT EXISTS (
            SELECT 1 FROM canonical_test_cases ctc
            WHERE ctc.project_id = tr.project_id
              AND ctc.test_fingerprint = tc.test_fingerprint
        )
        ORDER BY tr.project_id, tc.test_fingerprint, tc.created_at DESC
        """
    )

    # 6) Wire every existing test_case row to its canonical entry.
    op.execute(
        """
        UPDATE test_cases tc
        SET canonical_test_case_id = ctc.id
        FROM canonical_test_cases ctc, test_runs tr
        WHERE tc.test_run_id = tr.id
          AND ctc.project_id = tr.project_id
          AND ctc.test_fingerprint = tc.test_fingerprint
          AND tc.canonical_test_case_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_test_cases_canonical", table_name="test_cases")
    op.drop_column("test_cases", "canonical_test_case_id")

    op.drop_index("ix_ctc_fingerprint", table_name="canonical_test_cases")
    op.drop_index("ix_ctc_project_status", table_name="canonical_test_cases")
    op.drop_index("ix_ctc_test_suite_id", table_name="canonical_test_cases")
    op.drop_index("ix_ctc_project_id", table_name="canonical_test_cases")
    op.drop_table("canonical_test_cases")

    op.drop_index("ix_test_suites_project_default", table_name="test_suites")
    op.drop_index("ix_test_suites_project_id", table_name="test_suites")
    op.drop_table("test_suites")
