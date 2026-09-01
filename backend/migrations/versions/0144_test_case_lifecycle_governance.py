"""Add the committed test-case lifecycle and canonical-link foundation.

The migration is deliberately limited to S0, S2 and S-P.  Policy tables,
review clocks, health scores and the automation-governance state machine are
not part of this revision.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0144"
down_revision = "0143"
branch_labels = None
depends_on = None


_FLAG_KEY = "test_case_lifecycle_v2"
# A stable id lets downgrade distinguish the row seeded by this migration from
# an operator-owned row that already used the same key.  ``ON CONFLICT`` keeps
# upgrade non-destructive; the id predicate keeps downgrade ownership-safe.
_FLAG_ID = "7e3384b4-1a9f-4d52-b19a-a08f866195c9"


def upgrade() -> None:
    # Migration 0140 made this column non-null, then accidentally removed the
    # server default even though the ORM still declares ``server_default``.
    # Raw/older ingestion writers (including the protected CI fixture) may
    # legitimately omit the additive field during a rolling upgrade. Restore
    # the model-authoritative default before installing the lifecycle changes.
    op.alter_column(
        "test_cases",
        "steps_present",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=sa.text("false"),
    )

    # S0/S2 lifecycle attribution.  The audit log remains the authoritative
    # transition history; these columns make the current state cheap to render.
    op.add_column(
        "managed_test_cases",
        sa.Column("lifecycle_state_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column(
            "approved_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Promotion/dedup resolves the authored identity inside a project, while
    # the evidence-gap queue filters never-executed cases per project.  Keep
    # these non-unique: legacy authored rows may share automation evidence and
    # the service deterministically reuses the governed winner.
    op.create_index(
        "ix_mtc_project_fingerprint",
        "managed_test_cases",
        ["project_id", "test_fingerprint"],
    )
    op.create_index(
        "ix_mtc_project_last_executed",
        "managed_test_cases",
        ["project_id", "last_executed_at"],
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("needs_update_reason", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("deprecation_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column(
            "deprecated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_test_cases",
        sa.Column(
            "archived_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ``pending_review`` was emitted only by the RAG pre-acceptance path and is
    # not a lifecycle state.  Those rows are safe drafts; preserve their content
    # while bringing the stored vocabulary under the lifecycle enum.
    op.execute(
        "UPDATE managed_test_cases SET status = 'draft' "
        "WHERE status = 'pending_review'"
    )

    # The legacy AI-review path recorded completed AI evidence as an open
    # human review.  Close those rows before enforcing one human review per
    # case; AI evidence never owns a human review claim.
    op.execute(
        "UPDATE test_case_reviews SET status = 'ai_completed' "
        "WHERE status = 'in_progress' AND ai_review_completed IS TRUE "
        "AND reviewer_id IS NULL"
    )

    # Historical retries could create more than one open human review. Keep a
    # deterministic winner (claimed first, then oldest/id) and close every
    # loser without fabricating approval.  The partial unique index makes the
    # repaired invariant race-safe for all future writers.
    op.execute(
        """
        WITH ranked_open_reviews AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY test_case_id
                       ORDER BY CASE WHEN status = 'in_progress' THEN 0 ELSE 1 END,
                                created_at ASC NULLS LAST,
                                id ASC
                   ) AS open_rank
            FROM test_case_reviews
            WHERE status IN ('pending', 'in_progress')
        )
        UPDATE test_case_reviews AS review
        SET status = 'changes_requested',
            reviewer_id = NULL,
            reviewed_at = COALESCE(review.reviewed_at, now()),
            human_notes = COALESCE(
                review.human_notes,
                'Closed during lifecycle migration: duplicate open review'
            )
        FROM ranked_open_reviews AS ranked
        WHERE review.id = ranked.id AND ranked.open_rank > 1
        """
    )
    op.create_index(
        "uq_test_case_reviews_one_open_per_case",
        "test_case_reviews",
        ["test_case_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'in_progress')"),
    )

    # Full immutable authored-content snapshots plus optimistic-integrity
    # protection for concurrent saves.
    version_columns = (
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("preconditions", sa.Text(), nullable=True),
        sa.Column("test_data", sa.Text(), nullable=True),
        sa.Column("test_type", sa.String(length=50), nullable=True),
        sa.Column("priority", sa.String(length=20), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("feature_area", sa.String(length=500), nullable=True),
        sa.Column("suite_name", sa.String(length=500), nullable=True),
        sa.Column("test_suite_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("is_automated", sa.Boolean(), nullable=True),
        sa.Column("automation_status", sa.String(length=30), nullable=True),
        sa.Column("test_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=True),
    )
    for column in version_columns:
        op.add_column("test_case_versions", column)

    # Repair legacy duplicate/gapped version numbers without discarding an
    # immutable snapshot. The stable order retains the prior version ordering
    # and breaks ties by creation time/id. Align the owning row to the repaired
    # maximum so the next application save cannot collide.
    op.execute(
        """
        WITH ordered_versions AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY test_case_id
                       ORDER BY version ASC, created_at ASC NULLS LAST, id ASC
                   ) AS repaired_version
            FROM test_case_versions
        )
        UPDATE test_case_versions AS version_row
        SET version = ordered.repaired_version
        FROM ordered_versions AS ordered
        WHERE version_row.id = ordered.id
          AND version_row.version IS DISTINCT FROM ordered.repaired_version
        """
    )
    op.execute(
        """
        UPDATE managed_test_cases AS managed
        SET version = GREATEST(managed.version, versions.max_version)
        FROM (
            SELECT test_case_id, max(version) AS max_version
            FROM test_case_versions
            GROUP BY test_case_id
        ) AS versions
        WHERE managed.id = versions.test_case_id
        """
    )
    op.create_unique_constraint(
        "uq_test_case_versions_case_version",
        "test_case_versions",
        ["test_case_id", "version"],
    )

    op.add_column(
        "test_case_audit_logs",
        sa.Column("reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "test_case_audit_logs",
        sa.Column("policy_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "test_case_audit_logs",
        sa.Column("transition_from", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "test_case_audit_logs",
        sa.Column("transition_to", sa.String(length=30), nullable=True),
    )

    # S-P retirement confirmation and a retention-independent observation
    # timestamp for the derived orphan queue.
    op.add_column(
        "canonical_test_cases",
        sa.Column("retirement_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "canonical_test_cases",
        sa.Column(
            "retirement_confirmed_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "canonical_test_cases",
        sa.Column("retirement_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "canonical_test_cases",
        sa.Column("deleted_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ctc_project_unconfirmed_deletion",
        "canonical_test_cases",
        ["project_id", "deleted_observed_at"],
        postgresql_where=sa.text(
            "status = 'deleted' AND retirement_confirmed_at IS NULL"
        ),
    )

    # One authored identity may back at most one canonical automation identity.
    # Reconcile any historical fan-out deterministically before adding the
    # partial unique index. The canonical rows themselves remain intact.
    op.execute(
        """
        WITH ranked_managed_links AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY managed_test_case_id
                       ORDER BY updated_at DESC NULLS LAST, created_at ASC, id ASC
                   ) AS link_rank
            FROM canonical_test_cases
            WHERE managed_test_case_id IS NOT NULL
        )
        UPDATE canonical_test_cases AS canonical
        SET managed_test_case_id = NULL,
            source = 'execution'
        FROM ranked_managed_links AS ranked
        WHERE canonical.id = ranked.id AND ranked.link_rank > 1
        """
    )
    op.create_index(
        "uq_ctc_managed_test_case_id",
        "canonical_test_cases",
        ["managed_test_case_id"],
        unique=True,
        postgresql_where=sa.text("managed_test_case_id IS NOT NULL"),
    )

    op.execute(
        sa.text(
            "INSERT INTO feature_flags "
            "(id, key, description, enabled_global, rollout_percent, created_at, updated_at) "
            "VALUES (CAST(:flag_id AS uuid), :key, :description, true, 100, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(
            flag_id=_FLAG_ID,
            key=_FLAG_KEY,
            description="Use the complete governed lifecycle for authored test cases.",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM feature_flags "
            "WHERE id = CAST(:flag_id AS uuid) AND key = :key"
        ).bindparams(flag_id=_FLAG_ID, key=_FLAG_KEY)
    )

    # Preserve semantics within the vocabulary understood by the pre-0144
    # application. Archived stays terminal; needs_update becomes editable.
    op.execute(
        "UPDATE managed_test_cases SET status = 'draft' "
        "WHERE status = 'needs_update'"
    )
    op.execute(
        "UPDATE managed_test_cases SET status = 'deprecated' "
        "WHERE status = 'archived'"
    )
    # AI completion must not become a fake human approval or reopen a claimant.
    op.execute(
        "UPDATE test_case_reviews SET status = 'changes_requested' "
        "WHERE status = 'ai_completed'"
    )

    op.drop_index(
        "uq_ctc_managed_test_case_id", table_name="canonical_test_cases"
    )

    op.drop_index(
        "ix_ctc_project_unconfirmed_deletion", table_name="canonical_test_cases"
    )
    op.drop_column("canonical_test_cases", "deleted_observed_at")
    op.drop_column("canonical_test_cases", "retirement_reason")
    op.drop_column("canonical_test_cases", "retirement_confirmed_by_id")
    op.drop_column("canonical_test_cases", "retirement_confirmed_at")

    op.drop_column("test_case_audit_logs", "transition_to")
    op.drop_column("test_case_audit_logs", "transition_from")
    op.drop_column("test_case_audit_logs", "policy_snapshot")
    op.drop_column("test_case_audit_logs", "reason")

    op.drop_index(
        "uq_test_case_reviews_one_open_per_case", table_name="test_case_reviews"
    )

    op.drop_constraint(
        "uq_test_case_versions_case_version",
        "test_case_versions",
        type_="unique",
    )
    for name in (
        "changed_fields",
        "test_fingerprint",
        "automation_status",
        "is_automated",
        "estimated_duration_minutes",
        "tags",
        "test_suite_id",
        "suite_name",
        "feature_area",
        "severity",
        "priority",
        "test_type",
        "test_data",
        "preconditions",
        "objective",
    ):
        op.drop_column("test_case_versions", name)

    op.drop_column("managed_test_cases", "archived_by_id")
    op.drop_column("managed_test_cases", "archived_at")
    op.drop_column("managed_test_cases", "deprecated_by_id")
    op.drop_column("managed_test_cases", "deprecated_at")
    op.drop_column("managed_test_cases", "deprecation_reason")
    op.drop_column("managed_test_cases", "needs_update_reason")
    op.drop_column("managed_test_cases", "approved_by_id")
    op.drop_column("managed_test_cases", "approved_at")
    op.drop_column("managed_test_cases", "lifecycle_state_changed_at")
    op.drop_index("ix_mtc_project_last_executed", table_name="managed_test_cases")
    op.drop_index("ix_mtc_project_fingerprint", table_name="managed_test_cases")

    # Return to the exact 0143 schema. The ORM remains safe because application
    # inserts provide a Python-side default; 0144 alone owns the server repair.
    op.alter_column(
        "test_cases",
        "steps_present",
        existing_type=sa.Boolean(),
        existing_nullable=False,
        server_default=None,
    )
