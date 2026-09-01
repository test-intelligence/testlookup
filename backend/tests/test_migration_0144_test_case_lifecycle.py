"""Schema contract for the committed S0/S2/S-P migration."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import UniqueConstraint

from app.models.postgres import (
    CanonicalTestCase,
    ManagedTestCase,
    TestCaseAuditLog as AuditLogModel,
    TestCaseLifecycleState as LifecycleState,
    TestCaseReview as ReviewModel,
    TestCaseVersion as VersionModel,
)


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations/versions/0144_test_case_lifecycle_governance.py"
)


def test_migration_0144_is_linear_additive_and_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0144"' in source
    assert 'down_revision = "0143"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source
    assert "uq_test_case_versions_case_version" in source
    assert "ix_ctc_project_unconfirmed_deletion" in source
    assert "test_case_lifecycle_v2" in source
    assert "enabled_global, rollout_percent" in source
    assert "true, 100" in source


def test_migration_restores_the_steps_present_server_default():
    """Raw and old-version writers must survive the additive 0140 column."""
    source = MIGRATION.read_text(encoding="utf-8")
    upgrade, downgrade = source.split("def downgrade() -> None:", 1)
    assert '"steps_present"' in upgrade
    assert 'server_default=sa.text("false")' in upgrade
    assert '"steps_present"' in downgrade
    assert "server_default=None" in downgrade


def test_pending_review_is_reconciled_to_draft_without_approving_it():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "UPDATE managed_test_cases SET status = 'draft'" in source
    assert "WHERE status = 'pending_review'" in source
    assert "pending_review'" in source
    assert "pending_review' SET status = 'approved'" not in source


def test_completed_legacy_ai_reviews_are_closed_without_human_approval():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "SET status = 'ai_completed'" in source
    assert "ai_review_completed IS TRUE" in source
    assert "reviewer_id IS NULL" in source


def test_duplicate_open_reviews_are_deterministically_reconciled_before_index():
    source = MIGRATION.read_text(encoding="utf-8")
    reconcile = source.index("WITH ranked_open_reviews")
    index = source.index('"uq_test_case_reviews_one_open_per_case"')
    assert reconcile < index
    assert "PARTITION BY test_case_id" in source
    assert "CASE WHEN status = 'in_progress' THEN 0 ELSE 1 END" in source
    assert "created_at ASC NULLS LAST" in source
    assert "open_rank > 1" in source
    assert "SET status = 'changes_requested'" in source

    review_index = next(
        item
        for item in ReviewModel.__table__.indexes
        if item.name == "uq_test_case_reviews_one_open_per_case"
    )
    assert review_index.unique is True
    assert str(review_index.dialect_options["postgresql"]["where"]) == (
        "status IN ('pending', 'in_progress')"
    )


def test_migration_does_not_rewrite_existing_approved_cases_during_window():
    """Approved remains plan-eligible during the compatibility window."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "SET status = 'active' WHERE status = 'approved'" not in source


def test_managed_case_model_declares_the_complete_lifecycle_vocabulary():
    assert {state.value for state in LifecycleState} == {
        "draft",
        "review_requested",
        "under_review",
        "approved",
        "active",
        "rejected",
        "needs_update",
        "deprecated",
        "archived",
    }
    default = ManagedTestCase.__table__.columns.status.default.arg
    assert default == LifecycleState.DRAFT.value


def test_s0_s2_sp_columns_match_the_orm():
    managed = ManagedTestCase.__table__.columns
    for name in (
        "lifecycle_state_changed_at",
        "approved_at",
        "approved_by_id",
        "needs_update_reason",
        "deprecation_reason",
        "deprecated_at",
        "deprecated_by_id",
        "archived_at",
        "archived_by_id",
    ):
        assert name in managed

    version = VersionModel.__table__.columns
    for name in (
        "objective",
        "preconditions",
        "test_data",
        "test_type",
        "priority",
        "severity",
        "feature_area",
        "suite_name",
        "test_suite_id",
        "tags",
        "estimated_duration_minutes",
        "is_automated",
        "automation_status",
        "test_fingerprint",
        "changed_fields",
    ):
        assert name in version

    audit = AuditLogModel.__table__.columns
    assert {"reason", "policy_snapshot", "transition_from", "transition_to"} <= set(
        audit.keys()
    )

    canonical = CanonicalTestCase.__table__.columns
    assert {
        "retirement_confirmed_at",
        "retirement_confirmed_by_id",
        "retirement_reason",
        "deleted_observed_at",
    } <= set(canonical.keys())


def test_version_constraint_prevents_concurrent_duplicate_numbers():
    constraints = {
        (constraint.name, tuple(column.name for column in constraint.columns))
        for constraint in VersionModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert (
        "uq_test_case_versions_case_version",
        ("test_case_id", "version"),
    ) in constraints


def test_legacy_version_duplicates_are_renumbered_before_unique_constraint():
    source = MIGRATION.read_text(encoding="utf-8")
    repair = source.index("WITH ordered_versions")
    constraint = source.index('"uq_test_case_versions_case_version"')
    assert repair < constraint
    assert "PARTITION BY test_case_id" in source
    assert "ORDER BY version ASC, created_at ASC NULLS LAST, id ASC" in source
    assert "SET version = ordered.repaired_version" in source
    assert "GREATEST(managed.version, versions.max_version)" in source


def test_feature_flag_seed_and_downgrade_are_ownership_safe():
    source = MIGRATION.read_text(encoding="utf-8")
    assert '_FLAG_ID = "7e3384b4-1a9f-4d52-b19a-a08f866195c9"' in source
    assert "VALUES (CAST(:flag_id AS uuid)" in source
    assert "ON CONFLICT (key) DO NOTHING" in source
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    assert "WHERE id = CAST(:flag_id AS uuid) AND key = :key" in downgrade
    assert "DELETE FROM feature_flags WHERE key = :key" not in downgrade


def test_downgrade_maps_new_states_without_fabricating_approval_or_claims():
    downgrade = MIGRATION.read_text(encoding="utf-8").split(
        "def downgrade() -> None:", 1
    )[1]
    assert "SET status = 'draft'" in downgrade
    assert "WHERE status = 'needs_update'" in downgrade
    assert "SET status = 'deprecated'" in downgrade
    assert "WHERE status = 'archived'" in downgrade
    assert "SET status = 'changes_requested'" in downgrade
    assert "WHERE status = 'ai_completed'" in downgrade
    assert "SET status = 'approved'" not in downgrade


def test_canonical_managed_link_is_one_to_one_and_query_supported():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "WITH ranked_managed_links" in source
    assert "PARTITION BY managed_test_case_id" in source
    assert "link_rank > 1" in source
    assert '"uq_ctc_managed_test_case_id"' in source

    link_index = next(
        item
        for item in CanonicalTestCase.__table__.indexes
        if item.name == "uq_ctc_managed_test_case_id"
    )
    assert link_index.unique is True
    assert str(link_index.dialect_options["postgresql"]["where"]) == (
        "managed_test_case_id IS NOT NULL"
    )


def test_promotion_and_evidence_gap_queries_have_matching_managed_indexes():
    source = MIGRATION.read_text(encoding="utf-8")
    expected = {
        "ix_mtc_project_fingerprint": ("project_id", "test_fingerprint"),
        "ix_mtc_project_last_executed": ("project_id", "last_executed_at"),
    }
    model_indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in ManagedTestCase.__table__.indexes
    }
    for name, columns in expected.items():
        assert model_indexes[name] == columns
        assert f'"{name}"' in source
        assert f'op.drop_index("{name}"' in source

    # A shared automation fingerprint is not itself proof that two authored
    # cases are duplicates, so this lookup support must remain non-unique.
    fingerprint_index = next(
        index
        for index in ManagedTestCase.__table__.indexes
        if index.name == "ix_mtc_project_fingerprint"
    )
    assert fingerprint_index.unique is not True


def test_downgrade_removes_every_column_added_by_upgrade():
    source = MIGRATION.read_text(encoding="utf-8")
    added = {
        "lifecycle_state_changed_at",
        "approved_at",
        "approved_by_id",
        "needs_update_reason",
        "deprecation_reason",
        "deprecated_at",
        "deprecated_by_id",
        "archived_at",
        "archived_by_id",
        "objective",
        "preconditions",
        "test_data",
        "changed_fields",
        "reason",
        "policy_snapshot",
        "transition_from",
        "transition_to",
        "retirement_confirmed_at",
        "retirement_confirmed_by_id",
        "retirement_reason",
        "deleted_observed_at",
    }
    downgrade = source.split("def downgrade() -> None:", 1)[1]
    explicit = {
        "lifecycle_state_changed_at",
        "approved_at",
        "approved_by_id",
        "needs_update_reason",
        "deprecation_reason",
        "deprecated_at",
        "deprecated_by_id",
        "archived_at",
        "archived_by_id",
        "reason",
        "policy_snapshot",
        "transition_from",
        "transition_to",
        "retirement_confirmed_at",
        "retirement_confirmed_by_id",
        "retirement_reason",
        "deleted_observed_at",
    }
    for column in explicit:
        assert f'"{column}")' in downgrade, column
    for column in added - explicit:
        assert f'"{column}"' in downgrade, column
