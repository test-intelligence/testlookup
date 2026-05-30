"""
Tests for Custom Tags & Auto-Tagging (TG-1 through TG-8).

Covers:
  - Tag normalization and deduplication
  - System tag protection
  - Tag merge behavior
  - TestPlan and TestRun tag persistence in ORM
  - Auto-tagging constants and logic
  - Migration existence
"""
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TG-1: Tag Normalization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.tag_utils import (  # noqa: E402
    normalize_tag,
    normalize_tags,
    is_system_tag,
    validate_custom_tags,
    merge_tags,
    SYSTEM_TAGS,
    TAG_TYPE_CUSTOM,
    TAG_TYPE_SYSTEM,
)


class TestTagNormalization:
    def test_lowercase(self):
        assert normalize_tag("Regression") == "regression"

    def test_strip_whitespace(self):
        assert normalize_tag("  smoke  ") == "smoke"

    def test_spaces_to_hyphens(self):
        assert normalize_tag("high priority") == "high-priority"

    def test_remove_special_chars(self):
        assert normalize_tag("tag@#$%!") == "tag"

    def test_max_length(self):
        long_tag = "a" * 100
        assert len(normalize_tag(long_tag)) == 50

    def test_empty_tag(self):
        assert normalize_tag("") == ""

    def test_preserves_valid_chars(self):
        assert normalize_tag("api-v2.test_suite") == "api-v2.test_suite"


class TestNormalizeTags:
    def test_deduplicates(self):
        result = normalize_tags(["smoke", "Smoke", "SMOKE"])
        assert result == ["smoke"]

    def test_preserves_order(self):
        result = normalize_tags(["beta", "alpha", "gamma"])
        assert result == ["beta", "alpha", "gamma"]

    def test_removes_empty(self):
        result = normalize_tags(["valid", "", "  ", "also-valid"])
        assert result == ["valid", "also-valid"]


class TestSystemTagProtection:
    def test_system_tags_defined(self):
        assert "passed" in SYSTEM_TAGS
        assert "failed" in SYSTEM_TAGS
        assert "flaky" in SYSTEM_TAGS
        assert "regression" in SYSTEM_TAGS
        assert "duplicate" in SYSTEM_TAGS

    def test_is_system_tag(self):
        assert is_system_tag("passed") is True
        assert is_system_tag("FLAKY") is True  # case insensitive
        assert is_system_tag("my-custom-tag") is False

    def test_validate_custom_tags_removes_system(self):
        result = validate_custom_tags(["smoke", "passed", "regression", "api-test"])
        assert result == ["smoke", "api-test"]
        assert "passed" not in result
        assert "regression" not in result


class TestMergeTags:
    def test_merge_new_tags(self):
        result = merge_tags(["smoke"], ["api", "e2e"])
        assert result == ["smoke", "api", "e2e"]

    def test_merge_deduplicates(self):
        result = merge_tags(["smoke", "api"], ["api", "e2e"])
        assert result == ["smoke", "api", "e2e"]

    def test_merge_with_none_existing(self):
        result = merge_tags(None, ["smoke"])
        assert result == ["smoke"]

    def test_merge_preserves_order(self):
        result = merge_tags(["c", "a"], ["b", "a"])
        assert result == ["c", "a", "b"]


class TestTagTypeConstants:
    def test_type_values(self):
        assert TAG_TYPE_CUSTOM == "custom"
        assert TAG_TYPE_SYSTEM == "system"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TG-1/3: ORM Model Tag Columns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.postgres import TestPlan, TestRun, TestCase, SuiteMembership  # noqa: E402


class TestOrmTagColumns:
    def test_test_plan_has_tags(self):
        assert "tags" in {c.name for c in TestPlan.__table__.columns}

    def test_test_run_has_tags(self):
        assert "tags" in {c.name for c in TestRun.__table__.columns}

    def test_test_case_has_tags(self):
        assert "tags" in {c.name for c in TestCase.__table__.columns}

    def test_suite_membership_has_tags(self):
        assert "tags" in {c.name for c in SuiteMembership.__table__.columns}

    def test_tags_columns_nullable(self):
        for model in [TestPlan, TestRun, SuiteMembership]:
            col = model.__table__.c.tags
            assert col.nullable is True, f"{model.__tablename__}.tags should be nullable"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TG-3: Schema Tag Fields
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import TestPlanCreate, TestPlanResponse  # noqa: E402


class TestPlanTagSchemas:
    def test_create_with_tags(self):
        plan = TestPlanCreate(
            project_id=uuid.uuid4(),
            name="Release 2.0 Plan",
            tags=["release-2.0", "critical", "smoke"],
        )
        assert plan.tags == ["release-2.0", "critical", "smoke"]

    def test_create_without_tags(self):
        plan = TestPlanCreate(
            project_id=uuid.uuid4(),
            name="Basic Plan",
        )
        assert plan.tags is None

    def test_response_includes_tags(self):
        resp = TestPlanResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="Plan",
            status="active",
            ai_generated=False,
            total_cases=10,
            executed_cases=5,
            passed_cases=4,
            failed_cases=1,
            blocked_cases=0,
            tags=["smoke", "api"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert resp.tags == ["smoke", "api"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TG-5: Auto-Tagging Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestAutoTaggingConstants:
    def test_outcome_tags_are_system(self):
        for tag in ["passed", "failed", "skipped", "broken"]:
            assert tag in SYSTEM_TAGS

    def test_signal_tags_are_system(self):
        for tag in ["flaky", "regression", "duplicate"]:
            assert tag in SYSTEM_TAGS

    def test_run_summary_tags_are_system(self):
        for tag in ["all_passed", "has_failures", "has_skips", "flaky_content", "regression_detected"]:
            assert tag in SYSTEM_TAGS


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Migration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMigration:
    def test_migration_0050_exists(self):
        from pathlib import Path
        migration_dir = Path(__file__).parent.parent / "migrations" / "versions"
        files = [f.name for f in migration_dir.iterdir() if f.name.startswith("0050")]
        assert len(files) == 1
        assert "tags" in files[0]
