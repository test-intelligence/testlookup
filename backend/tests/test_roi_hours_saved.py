"""
US-12.1 / US-12.2 — engineer-hours-saved model.

Covers:

* Effective-assumptions resolution — defaults with no row, ``custom``
  source when a row exists, ``None`` project → defaults.
* ``upsert_assumptions`` — stage-only (flush, NEVER commit — the
  transaction-boundary ratchet), create-with-defaults, partial update.
* PUT bounds — ``ValueMetricAssumptionsWrite`` 422s outside 0 < x <= 480.
* Leg math — ``compute_leg_hours`` from fixed counts × assumptions.
* Availability gate — no runs / < 14 days history / no signal / available.
* Monthly assembly — ISO "YYYY-MM-01" wire format, ascending, data months
  plus current only.
* ``get_hours_saved_model`` — pinned contract shape end-to-end (with the
  DB layer monkeypatched).
* Methodology endpoint — version, three legs, both research anchors.
* Quarantine-state parity with ``flaky_quarantine_service``.
* Digest line/tile — present when available, absent when unavailable and
  on zero-change windows.
* Migration 0112 chain sanity (0111 → 0112, downgrade implemented).
"""
from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.services import value_metrics_service as vms  # noqa: E402

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── Effective assumptions ───────────────────────────────────────────────────


class TestEffectiveAssumptions:
    def test_defaults(self):
        a = vms.EffectiveAssumptions()
        assert a.triage_minutes_per_failure == 20.0
        assert a.blocked_run_wait_minutes == 30.0
        assert a.defect_filing_minutes == 15.0
        assert a.source == "default"

    async def test_none_project_resolves_to_defaults(self):
        db = MagicMock()
        a = await vms.get_effective_assumptions(db, None)
        assert a == vms.EffectiveAssumptions()
        db.execute.assert_not_called()

    async def test_missing_row_resolves_to_defaults(self):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        a = await vms.get_effective_assumptions(db, uuid.uuid4())
        assert a.source == "default"
        assert a.as_dict() == {
            "triage_minutes_per_failure": 20.0,
            "blocked_run_wait_minutes": 30.0,
            "defect_filing_minutes": 15.0,
        }

    async def test_row_flips_source_to_custom(self):
        row = MagicMock(
            triage_minutes_per_failure=45.0,
            blocked_run_wait_minutes=10.0,
            defect_filing_minutes=5.0,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        a = await vms.get_effective_assumptions(db, uuid.uuid4())
        assert a.source == "custom"
        assert a.triage_minutes_per_failure == 45.0
        assert a.blocked_run_wait_minutes == 10.0
        assert a.defect_filing_minutes == 5.0


class TestUpsertAssumptions:
    def _db(self, existing_row=None):
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing_row
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        return db

    async def test_create_fills_missing_fields_with_defaults(self):
        db = self._db(existing_row=None)
        row = await vms.upsert_assumptions(
            db, uuid.uuid4(), actor_id=None, triage_minutes_per_failure=60.0,
        )
        db.add.assert_called_once()
        assert row.triage_minutes_per_failure == 60.0
        assert row.blocked_run_wait_minutes == 30.0
        assert row.defect_filing_minutes == 15.0

    async def test_partial_update_keeps_unset_fields(self):
        existing = MagicMock(
            triage_minutes_per_failure=45.0,
            blocked_run_wait_minutes=10.0,
            defect_filing_minutes=5.0,
        )
        db = self._db(existing_row=existing)
        actor = uuid.uuid4()
        row = await vms.upsert_assumptions(
            db, uuid.uuid4(), actor_id=actor, defect_filing_minutes=7.5,
        )
        assert row is existing
        assert row.triage_minutes_per_failure == 45.0
        assert row.defect_filing_minutes == 7.5
        assert row.updated_by_user_id == actor
        db.add.assert_not_called()

    async def test_service_never_commits_or_rolls_back(self):
        # Transaction-boundary ratchet: the service stages; the router
        # session owns the commit.
        db = self._db(existing_row=None)
        await vms.upsert_assumptions(db, uuid.uuid4())
        db.flush.assert_awaited()
        db.commit.assert_not_called()
        db.rollback.assert_not_called()


class TestWriteSchemaBounds:
    def test_bounds(self):
        from pydantic import ValidationError

        from app.models.schemas import ValueMetricAssumptionsWrite

        # All-optional: empty body is valid (no-op PUT).
        ValueMetricAssumptionsWrite()
        # Edge values inside the bounds.
        ok = ValueMetricAssumptionsWrite(
            triage_minutes_per_failure=480,
            blocked_run_wait_minutes=0.5,
            defect_filing_minutes=1,
        )
        assert ok.triage_minutes_per_failure == 480
        # Outside the bounds → validation error (the router surfaces 422).
        for field, bad in [
            ("triage_minutes_per_failure", 0),
            ("triage_minutes_per_failure", -5),
            ("blocked_run_wait_minutes", 481),
            ("defect_filing_minutes", 0),
        ]:
            with pytest.raises(ValidationError):
                ValueMetricAssumptionsWrite(**{field: bad})


# ── Pure model math ─────────────────────────────────────────────────────────


class TestLegMath:
    def test_each_leg_from_fixed_counts(self):
        a = vms.EffectiveAssumptions()  # 20 / 30 / 15
        counts = {
            "clustered_failures": 12,       # 12 × 20 / 60 = 4.0 h
            "runs_unblocked_proxy": 4,      # 4 × 30 / 60 = 2.0 h
            "duplicates_absorbed": 8,       # 8 × 15 / 60 = 2.0 h
            "auto_triaged": 99,             # context only — no leg
            "quarantine_suppressed_failures": 50,  # context only — no leg
        }
        hours = vms.compute_leg_hours(counts, a)
        assert hours == {
            "hours_triage": 4.0,
            "hours_quarantine": 2.0,
            "hours_dedup": 2.0,
            "hours_total": 8.0,
        }

    def test_custom_assumptions_scale_the_legs(self):
        a = vms.EffectiveAssumptions(
            triage_minutes_per_failure=30.0,
            blocked_run_wait_minutes=60.0,
            defect_filing_minutes=6.0,
            source="custom",
        )
        hours = vms.compute_leg_hours(
            {"clustered_failures": 2, "runs_unblocked_proxy": 1, "duplicates_absorbed": 10},
            a,
        )
        assert hours["hours_triage"] == 1.0
        assert hours["hours_quarantine"] == 1.0
        assert hours["hours_dedup"] == 1.0
        assert hours["hours_total"] == 3.0

    def test_zero_counts_zero_hours(self):
        hours = vms.compute_leg_hours({}, vms.EffectiveAssumptions())
        assert hours["hours_total"] == 0.0


class TestAvailabilityGate:
    def test_no_runs(self):
        available, reason = vms.resolve_availability(None, None, 10.0)
        assert available is False
        assert reason == "no runs ingested yet"

    def test_fewer_than_14_days(self):
        available, reason = vms.resolve_availability(
            NOW - timedelta(days=5), NOW, 10.0,
        )
        assert available is False
        assert reason == "fewer than 14 days of ingested runs"

    def test_no_signal_in_window(self):
        available, reason = vms.resolve_availability(
            NOW - timedelta(days=60), NOW, 0.0,
        )
        assert available is False
        assert reason == "no automation signal in the last 30 days"

    def test_available(self):
        available, reason = vms.resolve_availability(
            NOW - timedelta(days=14), NOW, 0.5,
        )
        assert available is True
        assert reason is None


class TestMonthlyAssembly:
    def test_iso_wire_format_ascending_with_current_month(self):
        a = vms.EffectiveAssumptions()
        counts_by_month = {
            "2026-05-01": {"clustered_failures": 6, "duplicates_absorbed": 4},
            "2026-07-01": {"runs_unblocked_proxy": 2, "auto_triaged": 3},
        }
        rows = vms.assemble_monthly_rows(counts_by_month, a, now=NOW)
        assert [r["month"] for r in rows] == ["2026-05-01", "2026-07-01", "2026-08-01"]
        may = rows[0]
        assert may["hours_triage"] == 2.0    # 6 × 20 / 60
        assert may["hours_dedup"] == 1.0     # 4 × 15 / 60
        assert may["hours_total"] == 3.0
        # Current month present even with zero data; empty June dropped.
        assert rows[-1]["hours_total"] == 0.0
        # Every row carries the full pinned key set.
        expected_keys = {
            "month", "auto_triaged", "clustered_failures", "duplicates_absorbed",
            "quarantine_suppressed_failures", "runs_unblocked_proxy",
            "hours_triage", "hours_quarantine", "hours_dedup", "hours_total",
        }
        assert all(set(r.keys()) == expected_keys for r in rows)

    def test_months_back_year_boundary(self):
        assert vms._months_back(datetime(2026, 2, 15, tzinfo=timezone.utc), 5) == datetime(
            2025, 9, 1, tzinfo=timezone.utc
        )


# ── get_hours_saved_model contract shape ────────────────────────────────────


class TestHoursSavedModel:
    @pytest.fixture(autouse=True)
    def _no_cache(self, monkeypatch: pytest.MonkeyPatch):
        from app.services import cache_service

        monkeypatch.setattr(cache_service, "cache_get", AsyncMock(return_value=None))
        monkeypatch.setattr(cache_service, "cache_set", AsyncMock())
        monkeypatch.setattr(cache_service, "get_analytics_epoch", AsyncMock(return_value=0))

    async def test_available_project_shape(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            vms, "get_effective_assumptions",
            AsyncMock(return_value=vms.EffectiveAssumptions()),
        )
        monkeypatch.setattr(
            vms, "_run_span",
            AsyncMock(return_value=(NOW - timedelta(days=90), NOW)),
        )

        async def fake_gather(db, project_id, since, monthly):
            if not monthly:
                return {"_total": {"clustered_failures": 12, "runs_unblocked_proxy": 4,
                                   "duplicates_absorbed": 8}}
            return {"2026-07-01": {"clustered_failures": 12, "duplicates_absorbed": 8,
                                   "runs_unblocked_proxy": 4, "auto_triaged": 2}}

        monkeypatch.setattr(vms, "_gather_leg_counts", fake_gather)

        model = await vms.get_hours_saved_model(MagicMock(), uuid.uuid4(), months=6)
        assert model["available"] is True
        assert model["insufficient_data_reason"] is None
        assert model["headline"]["hours_saved_30d"] == 8.0
        assert model["headline"]["fte_equivalent_30d"] == round(8.0 / 173.2, 3)
        assert model["assumptions_source"] == "default"
        assert model["methodology_version"] == 1
        assert model["assumptions"]["triage_minutes_per_failure"] == 20.0
        months = [r["month"] for r in model["monthly"]]
        assert months == sorted(months)
        assert "2026-07-01" in months

    async def test_fresh_project_gated_and_zeroed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            vms, "get_effective_assumptions",
            AsyncMock(return_value=vms.EffectiveAssumptions()),
        )
        monkeypatch.setattr(
            vms, "_run_span",
            AsyncMock(return_value=(NOW - timedelta(days=3), NOW)),
        )
        monkeypatch.setattr(
            vms, "_gather_leg_counts",
            AsyncMock(return_value={"_total": {"clustered_failures": 100}}),
        )
        model = await vms.get_hours_saved_model(MagicMock(), uuid.uuid4())
        assert model["available"] is False
        assert model["insufficient_data_reason"] == "fewer than 14 days of ingested runs"
        # Headline zeroed when gated — even though raw counts existed.
        assert model["headline"] == {"hours_saved_30d": 0.0, "fte_equivalent_30d": 0.0}

    async def test_no_project_returns_unavailable_model(self):
        db = MagicMock()
        model = await vms.get_hours_saved_model(db, None)
        assert model["available"] is False
        assert model["monthly"] == []
        assert model["assumptions_source"] == "default"
        db.execute.assert_not_called()


# ── Methodology ─────────────────────────────────────────────────────────────


class TestMethodology:
    def test_shape_and_research_anchors(self):
        m = vms.get_methodology()
        assert m["version"] == 1
        assert [leg["key"] for leg in m["legs"]] == ["triage", "quarantine", "dedup"]
        for leg in m["legs"]:
            assert set(leg.keys()) == {"key", "title", "formula", "inputs", "caveats"}
            assert leg["caveats"]
        assert m["defaults"] == {
            "triage_minutes_per_failure": 20.0,
            "blocked_run_wait_minutes": 30.0,
            "defect_filing_minutes": 15.0,
        }
        notes = " ".join(m["research_notes"])
        assert "3 engineer-hours" in notes          # ~3 h per non-trivial failure
        assert "15–25 minutes" in notes             # refocus/context-switch band
        assert "20" in notes

    def test_proxy_and_cluster_instance_caveats_present(self):
        m = vms.get_methodology()
        text = " ".join(
            " ".join(leg["caveats"]) for leg in m["legs"]
        )
        assert "NOT distinct defects" in text
        assert "PROXY" in text

    def test_router_endpoint_serves_methodology(self):
        from app.routers.value_metrics import get_methodology_page, router

        assert callable(get_methodology_page)
        assert any(
            getattr(r, "path", "") == "/api/v1/value-metrics/methodology"
            for r in router.routes
        )


# ── Quarantine-state parity ─────────────────────────────────────────────────


def test_active_quarantine_states_match_flaky_quarantine_service():
    from app.services.flaky_quarantine_service import _ACTIVE_QUARANTINE_STATES

    assert tuple(vms.ACTIVE_QUARANTINE_STATES) == tuple(_ACTIVE_QUARANTINE_STATES)


# ── Assumptions router registration ─────────────────────────────────────────


class TestAssumptionsRouter:
    def test_router_prefix_and_paths(self):
        from app.routers.value_metric_assumptions import router

        assert router.prefix == "/api/v1/projects"
        paths = {(list(r.methods)[0], r.path) for r in router.routes}
        assert ("GET", "/api/v1/projects/{project_id}/value-metrics/assumptions") in paths
        assert ("PUT", "/api/v1/projects/{project_id}/value-metrics/assumptions") in paths

    def test_put_requires_qa_lead_and_project_access(self):
        # Authorization ratchet: {project_id} routes must carry
        # require_project_access; the PUT additionally gates on QA_LEAD.
        import inspect

        from app.routers import value_metric_assumptions as mod

        src = inspect.getsource(mod)
        assert "require_project_access()" in src
        # Re-audit N26: opted in for a project-bound key; require_project_access confines it.
        assert "require_role(UserRole.QA_LEAD, allow_project_key=True)" in src


# ── Digest line + tile (US-12.2) ────────────────────────────────────────────


class TestDigestHeadline:
    def _digest(self, **overrides) -> dict:
        base = {
            "project_name": "acme",
            "period": "weekly",
            "generated_at": NOW.isoformat(),
            "total_runs": 12,
            "avg_pass_rate": 96.5,
            "pass_rate_trend": 1.2,
            "new_regressions": 0,
            "flaky_test_count": 2,
            "action_items": [],
            "top_blockers": [],
            "top_clusters": [],
        }
        base.update(overrides)
        return base

    def test_text_line_present_when_available(self):
        from app.services import digest_content_service as dcs

        text_out = dcs.render_digest_text(
            self._digest(value_headline_hours_30d=42.5)
        )
        assert "≈ 42.5 engineer-hours saved in the last 30 days" in text_out
        assert "/value-metrics" in text_out

    def test_text_line_absent_when_unavailable(self):
        from app.services import digest_content_service as dcs

        text_out = dcs.render_digest_text(self._digest())
        assert "engineer-hours saved" not in text_out

    def test_text_line_absent_on_zero_change(self):
        from app.services import digest_content_service as dcs

        text_out = dcs.render_digest_text(
            self._digest(value_headline_hours_30d=42.5, is_zero_change=True)
        )
        assert "engineer-hours saved" not in text_out

    def test_html_tile_present_when_available(self):
        from app.services import digest_content_service as dcs

        html = dcs.render_digest_html(self._digest(value_headline_hours_30d=42.5))
        assert "Eng-hours saved (30d)" in html
        assert "≈ 42.5 h" in html

    def test_html_tile_absent_when_unavailable_or_zero_change(self):
        from app.services import digest_content_service as dcs

        assert "Eng-hours saved" not in dcs.render_digest_html(self._digest())
        assert "Eng-hours saved" not in dcs.render_digest_html(
            self._digest(value_headline_hours_30d=42.5, is_zero_change=True)
        )

    async def test_compute_headline_reads_cached_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            vms, "get_hours_saved_model",
            AsyncMock(return_value={
                "available": True,
                "headline": {"hours_saved_30d": 12.3, "fte_equivalent_30d": 0.071},
            }),
        )
        out = await vms.compute_headline(MagicMock(), uuid.uuid4())
        assert out == {"available": True, "hours_saved_30d": 12.3}


# ── Migration 0112 chain sanity ─────────────────────────────────────────────


def test_migration_0112_chains_from_0111_with_downgrade():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "versions" / "0112_value_metric_assumptions.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0112", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0112"
    assert mod.down_revision == "0111"
    assert callable(mod.upgrade) and callable(mod.downgrade)
    # Downgrade actually implemented (drops the table), not a pass stub.
    import inspect

    assert "drop_table" in inspect.getsource(mod.downgrade)
