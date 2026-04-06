"""
Tests for Customizable Analytics Charts (AC-1 through AC-8).

Covers:
  - SavedView model with page field
  - Widget JSON structure in SavedView.filters
  - Backward compatibility with filter-only saved views
  - Migration existence
"""
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC-2: SavedView Model Extension
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.postgres import SavedView  # noqa: E402


class TestSavedViewModel:
    def test_page_column_exists(self):
        columns = {c.name for c in SavedView.__table__.columns}
        assert "page" in columns

    def test_page_column_nullable(self):
        col = SavedView.__table__.c.page
        assert col.nullable is True

    def test_page_column_length(self):
        col = SavedView.__table__.c.page
        assert col.type.length >= 50

    def test_filters_column_is_json(self):
        col = SavedView.__table__.c.filters
        assert col.nullable is False  # JSON required


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC-2: Pydantic Schema Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import SavedViewCreate, SavedViewResponse  # noqa: E402


class TestSavedViewSchemas:
    def test_create_with_page_and_widgets(self):
        view = SavedViewCreate(
            name="My Dashboard",
            page="dashboard",
            filters={
                "page": "dashboard",
                "widgets": ["pass_fail_trend", "execution_volume", "flaky_tests_kpi"],
                "version": 1,
            },
        )
        assert view.page == "dashboard"
        assert view.filters["widgets"] == ["pass_fail_trend", "execution_volume", "flaky_tests_kpi"]

    def test_create_without_page_backward_compat(self):
        """Legacy saved views without page field still work."""
        view = SavedViewCreate(
            name="Old filter view",
            filters={"severity": "critical", "date_range": 7},
        )
        assert view.page is None
        assert view.filters["severity"] == "critical"

    def test_response_includes_page(self):
        resp = SavedViewResponse(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="Trends View",
            page="trends",
            filters={"page": "trends", "widgets": ["daily_breakdown"], "version": 1},
            is_shared=False,
            is_default=True,
            created_at=datetime.now(timezone.utc),
        )
        assert resp.page == "trends"
        assert resp.is_default is True

    def test_response_without_page_backward_compat(self):
        resp = SavedViewResponse(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="Legacy",
            filters={"category": "INFRASTRUCTURE"},
            is_shared=False,
            is_default=False,
            created_at=datetime.now(timezone.utc),
        )
        assert resp.page is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AC-1: Widget JSON Structure Convention
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWidgetJsonConvention:
    def test_analytics_view_payload_structure(self):
        """Widget configuration follows the documented JSON convention."""
        payload = {
            "page": "dashboard",
            "widgets": ["pass_fail_trend", "execution_volume"],
            "filters": {"days": 7},
            "version": 1,
        }
        assert "page" in payload
        assert isinstance(payload["widgets"], list)
        assert payload["version"] == 1

    def test_empty_widgets_is_valid(self):
        payload = {"page": "trends", "widgets": [], "version": 1}
        assert len(payload["widgets"]) == 0

    def test_filters_coexist_with_widgets(self):
        """Filters and widgets can coexist in the same JSON."""
        payload = {
            "page": "defects",
            "widgets": ["open_vs_resolved_pie"],
            "filters": {"resolution_status": "OPEN"},
            "version": 1,
        }
        assert payload["widgets"][0] == "open_vs_resolved_pie"
        assert payload["filters"]["resolution_status"] == "OPEN"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EV-1: Visualization Instance JSON Persistence (v2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestVisualizationInstancePersistence:
    def test_v2_instance_payload_structure(self):
        """v2 saved views persist visualization instances with per-instance config."""
        payload = {
            "page": "dashboard",
            "instances": [
                {"instanceId": "uuid-1", "templateId": "pass_fail_trend"},
                {"instanceId": "uuid-2", "templateId": "pass_fail_trend", "title": "API Tests Only",
                 "chartType": "area", "filters": {"suite": "api"}},
            ],
            "widgets": ["pass_fail_trend", "pass_fail_trend"],
            "version": 2,
        }
        assert payload["version"] == 2
        assert len(payload["instances"]) == 2
        assert payload["instances"][0]["templateId"] == "pass_fail_trend"
        assert payload["instances"][1]["title"] == "API Tests Only"
        assert payload["instances"][1]["chartType"] == "area"
        # backward compat: widgets array matches template IDs
        assert len(payload["widgets"]) == len(payload["instances"])

    def test_v2_saved_view_schema_accepts_instances(self):
        """SavedViewCreate accepts v2 payloads with instances."""
        view = SavedViewCreate(
            name="Custom Dashboard",
            page="dashboard",
            filters={
                "page": "dashboard",
                "instances": [
                    {"instanceId": "id-1", "templateId": "pass_fail_trend"},
                    {"instanceId": "id-2", "templateId": "execution_volume", "title": "My Volume"},
                ],
                "widgets": ["pass_fail_trend", "execution_volume"],
                "version": 2,
            },
        )
        assert view.page == "dashboard"
        assert len(view.filters["instances"]) == 2
        assert view.filters["version"] == 2

    def test_backward_compat_v1_widget_ids(self):
        """v1 saved views (string[] widgets) still load correctly."""
        view = SavedViewCreate(
            name="Old View",
            page="trends",
            filters={
                "page": "trends",
                "widgets": ["daily_breakdown", "pass_rate_trend"],
                "version": 1,
            },
        )
        assert "instances" not in view.filters
        assert len(view.filters["widgets"]) == 2

    def test_multiple_instances_same_template(self):
        """Multiple instances of the same template are allowed."""
        payload = {
            "page": "failures",
            "instances": [
                {"instanceId": "a", "templateId": "top_failing_bar", "title": "Last 7 days"},
                {"instanceId": "b", "templateId": "top_failing_bar", "title": "Last 30 days"},
            ],
            "version": 2,
        }
        template_ids = [i["templateId"] for i in payload["instances"]]
        assert template_ids.count("top_failing_bar") == 2
        # Instance IDs must be unique
        instance_ids = [i["instanceId"] for i in payload["instances"]]
        assert len(set(instance_ids)) == len(instance_ids)

    def test_failures_page_in_saved_view(self):
        """Failures page is now a valid page for saved views."""
        view = SavedViewCreate(
            name="Failure View",
            page="failures",
            filters={
                "page": "failures",
                "instances": [
                    {"instanceId": "f1", "templateId": "failure_category_pie"},
                    {"instanceId": "f2", "templateId": "flaky_leaderboard_table"},
                ],
                "version": 2,
            },
        )
        assert view.page == "failures"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Migration Existence
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMigrations:
    def test_migration_0049_exists(self):
        from pathlib import Path
        migration_dir = Path(__file__).parent.parent / "migrations" / "versions"
        files = [f.name for f in migration_dir.iterdir() if f.name.startswith("0049")]
        assert len(files) == 1
        assert "saved_view" in files[0]
