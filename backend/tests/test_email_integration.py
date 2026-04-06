"""
Tests for Email Integration and Scheduled Reports (EM-1 through EM-8).

Covers:
  - dispatch_ai_summary_notifications: enriched notification dispatch
  - Email template rendering with executive panel
  - DigestSubscription model with new schedule types
  - Subscription schema validation
  - Per-run subscription matching logic
"""
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("asyncpg")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EM-7: Email Template Rendering
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.services.notification.email_templates import (  # noqa: E402
    render_executive_panel_email,
    render_metrics_strip_email,
    render_key_takeaways_email,
    render_action_items_email,
    render_release_signal_email,
)


class TestEmailTemplateRendering:
    def test_executive_panel_renders_headline(self):
        panel = {
            "headline": "Build 42 — 3 failures detected",
            "status_signal": "CONDITIONAL_GO",
            "risk_score": 45,
            "metrics": {"pass_rate": 94.0, "total_tests": 100, "failed": 3, "skipped": 2,
                        "failure_clusters": 1, "anomaly_count": 0},
            "dominant_failure": {"category": "PRODUCT_BUG", "count": 2, "percentage": 66.7},
            "key_takeaways": ["2 product bugs detected", "Pass rate dropped 3% from baseline"],
            "baseline_comparison": {"pass_rate_delta": -3.0, "new_failures": 2, "resolved": 1},
            "next_actions": ["Review recent commits", "Re-run failed suites"],
        }
        html = render_executive_panel_email(panel)
        assert "Build 42" in html
        assert "CONDITIONAL GO" in html
        assert "Risk 45/100" in html
        assert "94.0%" in html
        assert "Product Bug" in html
        assert "product bugs" in html
        assert "Review recent commits" in html

    def test_executive_panel_all_green(self):
        panel = {
            "headline": "Build 1 — All 50 tests passed",
            "status_signal": "GO",
            "risk_score": None,
            "metrics": {"pass_rate": 100.0, "total_tests": 50, "failed": 0, "skipped": 0,
                        "failure_clusters": 0, "anomaly_count": 0},
            "dominant_failure": None,
            "key_takeaways": ["All 50 tests passed"],
            "baseline_comparison": None,
            "next_actions": [],
        }
        html = render_executive_panel_email(panel)
        assert "All 50 tests passed" in html
        assert "GO" in html
        assert "Risk" not in html  # no risk score

    def test_executive_panel_empty_returns_empty(self):
        assert render_executive_panel_email({}) == ""
        assert render_executive_panel_email(None) == ""

    def test_metrics_strip_renders_values(self):
        html = render_metrics_strip_email({
            "pass_rate": 85.5, "total_tests": 200, "failed": 15,
        })
        assert "85.5%" in html
        assert "200" in html
        assert "15" in html

    def test_key_takeaways_renders_bullets(self):
        html = render_key_takeaways_email(["Issue A detected", "Pass rate declined"])
        assert "Issue A" in html
        assert "Pass rate declined" in html
        assert "Key Takeaways" in html

    def test_action_items_renders_numbered(self):
        html = render_action_items_email(["Fix bug", "Re-run tests", "Deploy hotfix"])
        assert "1." in html
        assert "2." in html
        assert "3." in html
        assert "Next Actions" in html

    def test_release_signal_go(self):
        html = render_release_signal_email("GO", risk_score=10)
        assert "GO" in html
        assert "Risk 10/100" in html

    def test_release_signal_no_go(self):
        html = render_release_signal_email("NO_GO")
        assert "NO GO" in html

    def test_html_escaping(self):
        panel = {
            "headline": "<script>alert('xss')</script>",
            "status_signal": "GO",
            "metrics": {"pass_rate": 100.0, "total_tests": 1, "failed": 0},
        }
        html = render_executive_panel_email(panel)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EM-2: Subscription Model Extension
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.postgres import DigestSchedule, DigestSubscription  # noqa: E402


class TestDigestScheduleEnum:
    def test_daily_and_weekly_still_exist(self):
        assert DigestSchedule.DAILY == "DAILY"
        assert DigestSchedule.WEEKLY == "WEEKLY"

    def test_new_event_driven_schedules(self):
        assert DigestSchedule.PER_RUN == "PER_RUN"
        assert DigestSchedule.PER_RELEASE == "PER_RELEASE"
        assert DigestSchedule.PER_SUITE == "PER_SUITE"

    def test_all_values(self):
        values = {s.value for s in DigestSchedule}
        assert values == {"DAILY", "WEEKLY", "PER_RUN", "PER_RELEASE", "PER_SUITE"}


class TestDigestSubscriptionModel:
    def test_new_columns_exist(self):
        columns = {c.name for c in DigestSubscription.__table__.columns}
        assert "scope_type" in columns
        assert "scope_value" in columns
        assert "trigger_filter" in columns

    def test_schedule_column_width(self):
        """Schedule column must be wide enough for PER_RELEASE (11 chars)."""
        col = DigestSubscription.__table__.c.schedule
        assert col.type.length >= 20


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EM-2: Schema Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import DigestSubscriptionCreate, DigestSubscriptionResponse  # noqa: E402
from pydantic import ValidationError  # noqa: E402


class TestDigestSubscriptionSchemas:
    def test_create_with_per_run(self):
        sub = DigestSubscriptionCreate(
            name="Per-run alerts",
            schedule="PER_RUN",
            trigger_filter="failed_only",
        )
        assert sub.schedule == "PER_RUN"
        assert sub.trigger_filter == "failed_only"

    def test_create_with_per_suite(self):
        sub = DigestSubscriptionCreate(
            name="Suite report",
            schedule="PER_SUITE",
            scope_type="suite",
            scope_value="smoke-tests",
        )
        assert sub.scope_type == "suite"
        assert sub.scope_value == "smoke-tests"

    def test_create_with_per_release(self):
        sub = DigestSubscriptionCreate(
            name="Release updates",
            schedule="PER_RELEASE",
            scope_type="release",
        )
        assert sub.schedule == "PER_RELEASE"

    def test_daily_still_works(self):
        sub = DigestSubscriptionCreate(name="Daily digest", schedule="DAILY")
        assert sub.schedule == "DAILY"
        assert sub.scope_type == "project"  # default
        assert sub.trigger_filter == "all"  # default

    def test_invalid_schedule_rejected(self):
        with pytest.raises(ValidationError):
            DigestSubscriptionCreate(name="Bad", schedule="HOURLY")

    def test_invalid_scope_type_rejected(self):
        with pytest.raises(ValidationError):
            DigestSubscriptionCreate(name="Bad", scope_type="invalid")

    def test_invalid_trigger_filter_rejected(self):
        with pytest.raises(ValidationError):
            DigestSubscriptionCreate(name="Bad", trigger_filter="always")

    def test_response_includes_new_fields(self):
        resp = DigestSubscriptionResponse(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            name="Test",
            schedule="PER_RUN",
            channel="email",
            is_active=True,
            is_paused=False,
            scope_type="project",
            scope_value=None,
            trigger_filter="failed_only",
            created_at=datetime.now(timezone.utc),
        )
        assert resp.scope_type == "project"
        assert resp.trigger_filter == "failed_only"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EM-1: Notification Manager — AI Summary Dispatch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.postgres import NotificationEventType  # noqa: E402


class TestNotificationEventTypes:
    def test_ai_analysis_complete_event_exists(self):
        assert NotificationEventType.AI_ANALYSIS_COMPLETE == "ai_analysis_complete"

    def test_all_expected_events(self):
        values = {e.value for e in NotificationEventType}
        assert "ai_analysis_complete" in values
        assert "run_failed" in values
        assert "run_passed" in values


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EM-8: AgentRunSummaryResponse includes executive_panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.models.schemas import AgentRunSummaryResponse  # noqa: E402


class TestAgentRunSummaryResponsePanel:
    def test_executive_panel_field_exists(self):
        resp = AgentRunSummaryResponse(
            test_run_id="run-1",
            executive_summary="Summary text",
            markdown_report="## Summary",
            executive_panel={"headline": "Build 1", "status_signal": "GO"},
        )
        assert resp.executive_panel is not None
        assert resp.executive_panel["headline"] == "Build 1"

    def test_executive_panel_defaults_to_none(self):
        resp = AgentRunSummaryResponse(
            test_run_id="run-1",
            executive_summary="Summary",
            markdown_report="## Report",
        )
        assert resp.executive_panel is None
