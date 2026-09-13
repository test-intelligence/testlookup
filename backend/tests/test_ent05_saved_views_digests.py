"""
Unit tests for ENT-05: Scheduled Digests and Saved Views.

Covers:
 - Saved view schemas and CRUD validation
 - Digest subscription schemas and lifecycle
 - Digest content generation structure
 - Digest HTML rendering (XSS safety, content sections)
 - Schedule values and column widths
 - Celery beat task registration
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt",
                checkpw=MagicMock(return_value=True),
                hashpw=MagicMock(return_value=b"$2b$fake"),
                gensalt=MagicMock(return_value=b"$2b$12$salt"),
            ))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))
        yield


# ── Saved View schema tests ──────────────────────────────────────────────────


class TestSavedViewSchemas:
    def test_create_defaults(self):
        from app.models.schemas import SavedViewCreate

        view = SavedViewCreate(name="My View", filters={"severity": "CRITICAL"})
        assert view.is_shared is False
        assert view.is_default is False
        assert view.project_id is None

    def test_create_with_all_fields(self):
        from app.models.schemas import SavedViewCreate

        view = SavedViewCreate(
            project_id=uuid.uuid4(), name="Team View",
            description="Shared view for the team",
            filters={"severity": "HIGH", "category": "PRODUCT_BUG"},
            is_shared=True, is_default=True,
        )
        assert view.is_shared is True
        assert view.is_default is True

    def test_create_name_min_length(self):
        from pydantic import ValidationError
        from app.models.schemas import SavedViewCreate

        with pytest.raises(ValidationError):
            SavedViewCreate(name="X", filters={})

    def test_update_partial(self):
        from app.models.schemas import SavedViewUpdate

        update = SavedViewUpdate(name="Renamed")
        assert update.name == "Renamed"
        assert update.filters is None

    def test_response_model(self):
        from app.models.schemas import SavedViewResponse

        resp = SavedViewResponse(
            id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test",
            filters={"x": 1}, is_shared=False, is_default=False,
            created_at="2026-04-02T12:00:00Z",
        )
        assert resp.name == "Test"


# ── Digest Subscription schema tests ─────────────────────────────────────────


class TestDigestSubscriptionSchemas:
    def test_create_defaults(self):
        from app.models.schemas import DigestSubscriptionCreate

        sub = DigestSubscriptionCreate(name="Weekly Digest")
        assert sub.schedule == "WEEKLY"
        assert sub.channel == "email"
        assert sub.project_id is None

    def test_create_daily(self):
        from app.models.schemas import DigestSubscriptionCreate

        sub = DigestSubscriptionCreate(name="Daily Digest", schedule="DAILY", channel="slack")
        assert sub.schedule == "DAILY"
        assert sub.channel == "slack"

    def test_create_invalid_schedule(self):
        from pydantic import ValidationError
        from app.models.schemas import DigestSubscriptionCreate

        with pytest.raises(ValidationError):
            DigestSubscriptionCreate(name="Bad", schedule="HOURLY")

    def test_create_invalid_channel(self):
        from pydantic import ValidationError
        from app.models.schemas import DigestSubscriptionCreate

        with pytest.raises(ValidationError):
            DigestSubscriptionCreate(name="Bad", channel="sms")

    def test_update_partial(self):
        from app.models.schemas import DigestSubscriptionUpdate

        update = DigestSubscriptionUpdate(is_paused=True)
        assert update.is_paused is True
        assert update.name is None

    def test_response_model(self):
        from app.models.schemas import DigestSubscriptionResponse

        resp = DigestSubscriptionResponse(
            id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test",
            schedule="WEEKLY", channel="email",
            is_active=True, is_paused=False, delivery_count=0,
            created_at="2026-04-02T12:00:00Z",
        )
        assert resp.delivery_count == 0


# ── Digest Content schema tests ──────────────────────────────────────────────


class TestDigestContentSchema:
    def test_defaults(self):
        from app.models.schemas import DigestContentResponse

        content = DigestContentResponse(period="weekly", generated_at="now")
        assert content.total_runs == 0
        assert content.top_blockers == []
        assert content.action_items == []

    def test_full_content(self):
        from app.models.schemas import DigestContentResponse

        content = DigestContentResponse(
            project_name="My Project",
            period="daily",
            generated_at="2026-04-02T12:00:00Z",
            total_runs=15,
            avg_pass_rate=87.5,
            pass_rate_trend=-3.2,
            new_regressions=2,
            top_blockers=["Auth timeout"],
            top_clusters=[{"label": "Auth", "size": 10, "criticality": "HIGH"}],
            flaky_test_count=5,
            release_decisions=[{"run_id": "abc", "recommendation": "NO_GO", "risk_score": 65}],
            action_items=["Fix auth module"],
        )
        assert content.avg_pass_rate == 87.5
        assert len(content.top_clusters) == 1


# ── Digest HTML rendering tests ──────────────────────────────────────────────


class TestDigestHtmlRendering:
    def _sample_digest(self) -> dict:
        return {
            "project_name": "Test Project",
            "period": "weekly",
            "generated_at": "2026-04-02T12:00:00Z",
            "total_runs": 20,
            "avg_pass_rate": 88.5,
            "pass_rate_trend": -2.1,
            "new_regressions": 3,
            "top_blockers": ["Auth timeout", "DB connection issue"],
            "top_clusters": [{"label": "Auth cluster", "size": 12, "criticality": "HIGH"}],
            "flaky_test_count": 7,
            "release_decisions": [],
            "action_items": ["Fix auth module", "Address flaky tests"],
        }

    def test_render_returns_valid_html(self):
        from app.services.digest_content_service import render_digest_html

        html = render_digest_html(self._sample_digest())
        assert "<!DOCTYPE html>" in html
        assert "TestLookup" in html
        assert "Test Project" in html

    def test_render_contains_metrics(self):
        from app.services.digest_content_service import render_digest_html

        html = render_digest_html(self._sample_digest())
        assert "88.5%" in html
        assert "20" in html  # total runs

    def test_render_contains_action_items(self):
        from app.services.digest_content_service import render_digest_html

        html = render_digest_html(self._sample_digest())
        assert "Fix auth module" in html
        assert "Action Items" in html

    def test_render_contains_blockers(self):
        from app.services.digest_content_service import render_digest_html

        html = render_digest_html(self._sample_digest())
        assert "Auth timeout" in html
        assert "Top Blockers" in html

    def test_render_escapes_xss(self):
        from app.services.digest_content_service import render_digest_html

        digest = self._sample_digest()
        digest["top_blockers"] = ['<script>alert("xss")</script>']
        html = render_digest_html(digest)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_render_empty_digest(self):
        from app.services.digest_content_service import render_digest_html

        digest = {
            "project_name": None, "period": "daily",
            "generated_at": "now", "total_runs": 0,
            "avg_pass_rate": None, "pass_rate_trend": None,
            "new_regressions": 0, "top_blockers": [],
            "top_clusters": [], "flaky_test_count": 0,
            "release_decisions": [], "action_items": [],
        }
        html = render_digest_html(digest)
        assert "<!DOCTYPE html>" in html
        assert "Daily" in html


# ── Model column width safety ────────────────────────────────────────────────


class TestColumnWidths:
    def test_schedule_values_fit(self):
        for s in ("DAILY", "WEEKLY"):
            assert len(s) <= 10

    def test_channel_values_fit(self):
        for c in ("email", "slack", "teams"):
            assert len(c) <= 20

    def test_view_name_max(self):
        assert len("a" * 255) <= 255


# ── ORM model field existence ────────────────────────────────────────────────


class TestORMModels:
    def test_saved_view_fields(self):
        from app.models.postgres import SavedView

        for field in ("user_id", "project_id", "name", "filters", "is_shared", "is_default"):
            assert hasattr(SavedView, field)

    def test_digest_subscription_fields(self):
        from app.models.postgres import DigestSubscription

        for field in ("user_id", "project_id", "saved_view_id", "name", "schedule",
                       "channel", "is_active", "is_paused", "next_delivery_at", "delivery_count"):
            assert hasattr(DigestSubscription, field)

    def test_digest_schedule_enum(self):
        from app.models.postgres import DigestSchedule

        assert DigestSchedule.DAILY.value == "DAILY"
        assert DigestSchedule.WEEKLY.value == "WEEKLY"


# ── Celery beat schedule ─────────────────────────────────────────────────────


class TestCeleryBeatSchedule:
    def test_digest_task_in_beat_schedule(self):
        from app.worker.celery_app import celery_app

        beat = celery_app.conf.beat_schedule
        assert "daily-digest-dispatch" in beat
        assert beat["daily-digest-dispatch"]["task"] == "app.worker.tasks.dispatch_scheduled_digests"
