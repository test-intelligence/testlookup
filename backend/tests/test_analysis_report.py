"""Attached HTML analysis report (PMF US-7.5).

Covers:

* ``render_analysis_report_html`` — section-by-section presence with
  fabricated service data, row caps + "+K more — open dashboard" overflow
  lines, HTML-escaping of hostile user strings (``<script>`` test name),
  graceful omission lines for empty/missing subsystems.
* ``fill_day_buckets`` — weekly day-bucket math (gap filling, weighted
  pass rate, multi-row same-day merge).
* ``report_attachment_filename`` — slugging + weekly suffix.
* ``enforce_size_bound`` — shrunken re-render, then the minimal fallback.
* Dashboard parity — the executive summary uses
  ``summary_report_service.build_summary_report``'s numbers verbatim.
* ``collect_analysis_report_data`` — a failing section records a reason
  instead of raising (never-raises discipline).
* ``build_digest_report_attachment`` — never raises (builder crash →
  ``None``; non-project subscription → ``None``); filename/html tuple on
  success.
* ``send_html_email_with_attachments`` — multipart/mixed MIME assembly:
  alternative body part intact + attachment part with filename, and the
  no-attachment path delegating to the plain digest email.
* Digest note helpers — Slack/Teams appended line + the apologetic email
  note (US-7.5 plumbing in ``digest_content_service``).
* Subscription schema — ``report_attachment`` create default OFF / partial
  update / response mapping.
* Migration 0107 chain sanity (0106 → 0107, downgrade implemented).
* Endpoint — ``text/html`` content type + ``Content-Disposition:
  attachment`` filename; ``require_project_access`` guard present.
"""
from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.services import analysis_report_service as ars  # noqa: E402

NOW = datetime(2026, 7, 10, 7, 0, tzinfo=timezone.utc)
HOSTILE = "<script>alert(1)</script>"


# ── Fabricated data envelope ────────────────────────────────────────────────


def _summary_payload(**overrides) -> dict:
    base = {
        "project_id": "p",
        "project_name": "Acme",
        "mode": "window",
        "window_days": 7,
        "totals": {
            "total_test_cases": 480,
            "passed": 440,
            "failed": 25,
            "skipped": 10,
            "broken": 5,
            "evaluated": 470,
            "pass_rate_pct": 91.7,
            "weighted_pass_rate_pct": 93.6,
        },
        "run_count": 42,
        "suites": [{"suite_name": "smoke"}, {"suite_name": "api"}],
        "top_failing_tests": [],
    }
    base.update(overrides)
    return base


def _section(data, ok=True, reason=None) -> dict:
    return {"ok": ok, "reason": reason, "data": data}


def _run_item(i: int, **overrides) -> dict:
    base = {
        "id": str(uuid.UUID(int=i)),
        "build_number": f"b{i}",
        "branch": "main",
        "pr_number": i if i % 2 else None,
        "status": "PASSED" if i % 3 else "FAILED",
        "pass_rate": 95.0,
        "duration_ms": 60_000 + i,
        "created_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


def _data(**section_overrides) -> dict:
    sections = {
        "summary": _section({
            "summary": _summary_payload(),
            "coverage_summary": {"total_executions": 5200, "suite_count": 2},
            "prior": {"weighted_pass_rate_pct": 90.1, "run_count": 40},
            "day_buckets": ars.fill_day_buckets(
                [(NOW - timedelta(days=3), 4, 90, 100)], NOW - timedelta(days=7), 7,
            ),
        }),
        "runs": _section({"items": [_run_item(i) for i in range(1, 11)], "total": 10}),
        "failures": _section({
            "delta": {
                "new_failures": 3,
                "new_failures_top": ["test_login", HOSTILE],
                "newly_flaky": 2,
                "recovered": 1,
                "quarantine_debt": {"active": 4, "stale": 1, "ready_to_promote": 2},
                "gate_change": None,
            },
            "top_failing": [
                {
                    "test_name": HOSTILE,
                    "suite_name": "smoke",
                    "fail_count": 9,
                    "failure_kind": "product",
                    "error_first_line": 'AssertionError: expected "x" got <b>y</b>',
                },
                {
                    "test_name": "test_checkout",
                    "suite_name": "api",
                    "fail_count": 5,
                    "failure_kind": "test_code",
                    "error_first_line": None,
                },
            ],
            "clusters": [
                {"label": "Timeout in payment <svc>", "size": 7, "classification": "regression"},
            ],
            "by_kind": [
                {"kind": "product", "count": 5},
                {"kind": "test_code", "count": 3},
                {"kind": "infrastructure", "count": 1},
                {"kind": "unknown", "count": 0},
            ],
        }),
        "flaky": _section({
            "known_flaky": 6,
            "newly_flaky": 2,
            "quarantine_debt": {"active": 4, "stale": 1, "ready_to_promote": 2},
            "top_flip_rate": [
                {"test_name": "test_flappy", "suite_name": "smoke",
                 "flip_rate": 0.42, "status": "QUARANTINED"},
            ],
        }),
        "slowest": _section({
            "items": [
                {"test_name": "test_slow", "suite_name": "e2e",
                 "duration_ms": 95_000, "status": "PASSED"},
            ],
        }),
        "gate": _section({
            "decision": {
                "recommendation": "CONDITIONAL_GO",
                "risk_score": 55,
                "conditions_for_go": ["[Policy] fix cluster cl_001"],
                "blocking_issues": [],
                "kind_counterfactual": "Would have been NO_GO; downgraded because 3 infrastructure failure(s) <= budget 5",
                "created_at": NOW.isoformat(),
            },
        }),
        "defects": _section({
            "items": [
                {
                    "jira_ticket_id": "ABC-123",
                    "jira_ticket_url": "https://jira/browse/ABC-123",
                    "jira_status": "Done",
                    "external_status_conflict": True,
                    "resolution_status": "OPEN",
                    "test_name": "test_login",
                    "failure_category": "PRODUCT_BUG",
                },
            ],
            "total": 1,
        }),
        "ownership": _section({
            "configured": True,
            "teams": [{"team": "payments", "tests": 4, "failures": 12}],
        }),
    }
    sections.update(section_overrides)
    return {
        "project": {"id": "pid", "name": "Acme <Corp>", "slug": "acme"},
        "window": {
            "key": "7d", "days": 7,
            "start": (NOW - timedelta(days=7)).isoformat(),
            "end": NOW.isoformat(),
        },
        "generated_at": NOW.isoformat(),
        "base_url": "http://dash.local",
        "caps": ars.DEFAULT_CAPS,
        "sections": sections,
    }


# ── Rendering: section presence ─────────────────────────────────────────────


def test_render_includes_all_nine_sections():
    html = ars.render_analysis_report_html(_data())
    for heading in [
        "TestLookup — Analysis Report",
        "Executive summary",
        "Runs",
        "Failures for investigation",
        "Flaky &amp; quarantine",
        "Slowest tests",
        "Release gate",
        "Open defects",
        "Failures by owning team",
    ]:
        assert heading in html, heading


def test_render_header_carries_window_run_count_and_deep_link():
    html = ars.render_analysis_report_html(_data())
    assert "Weekly (7d)" in html
    assert "10 runs" in html
    assert 'href="http://dash.local/overview"' in html
    assert "UTC" in html


def test_render_is_self_contained():
    html = ars.render_analysis_report_html(_data())
    # No external asset loads: every http(s) URL appears only inside href
    # anchors (deep links), never as src/import.
    assert "src=" not in html
    assert "@import" not in html
    assert "<link" not in html
    assert html.startswith("<!DOCTYPE html>")


def test_render_executive_summary_tiles_and_sparkline():
    html = ars.render_analysis_report_html(_data())
    assert "480" in html          # unique tests
    assert "5200" in html         # executions (coverage source)
    assert "93.6%" in html        # weighted pass rate
    assert "+3.5% vs prior 7d" in html  # 93.6 - 90.1
    assert "Runs + pass rate per day" in html
    assert "<svg" in html


def test_render_gate_counterfactual_and_conditions():
    html = ars.render_analysis_report_html(_data())
    assert "CONDITIONAL_GO" in html
    assert "[Policy] fix cluster cl_001" in html
    assert "Would have been NO_GO" in html


def test_render_defect_conflict_badge():
    html = ars.render_analysis_report_html(_data())
    assert "ABC-123" in html
    assert "closed in Jira but still failing" in html


def test_render_ownership_not_configured_line():
    data = _data(ownership=_section({"configured": False, "teams": []}))
    html = ars.render_analysis_report_html(data)
    assert "No ownership rules configured" in html
    assert "/ownership" in html


# ── Rendering: caps + overflow ──────────────────────────────────────────────


def test_runs_table_caps_at_50_with_overflow_line():
    items = [_run_item(i) for i in range(1, 61)]
    data = _data(runs=_section({"items": items, "total": 60}))
    html = ars.render_analysis_report_html(data)
    assert "#b50" in html
    assert "#b51" not in html
    assert "+10 more" in html
    assert "open dashboard" in html


def test_top_failing_caps_at_20_with_overflow_line():
    top = [
        {"test_name": f"t{i}", "suite_name": "s", "fail_count": 30 - i,
         "failure_kind": "unknown", "error_first_line": None}
        for i in range(1, 22)
    ]
    data = _data()
    data["sections"]["failures"]["data"]["top_failing"] = top
    html = ars.render_analysis_report_html(data)
    assert ">t20<" in html
    assert ">t21<" not in html
    assert "+1 more" in html


# ── Rendering: escaping ─────────────────────────────────────────────────────


def test_hostile_test_name_is_escaped_everywhere():
    html = ars.render_analysis_report_html(_data())
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # Error message and cluster label markup also escaped.
    assert "<b>y</b>" not in html
    assert "<svc>" not in html
    # Project name too.
    assert "Acme &lt;Corp&gt;" in html


# ── Rendering: graceful omission ────────────────────────────────────────────


def test_failed_section_renders_reason_line_instead_of_body():
    data = _data(gate=_section(None, ok=False, reason="Release gate data unavailable."))
    html = ars.render_analysis_report_html(data)
    assert "Release gate data unavailable." in html
    assert "risk score" not in html


def test_empty_window_sections_render_explanatory_lines():
    data = _data(
        runs=_section({"items": [], "total": 0}),
        failures=_section({"delta": None, "top_failing": [], "clusters": [], "by_kind": []}),
        slowest=_section({"items": []}),
        gate=_section({"decision": None}),
        defects=_section({"items": [], "total": 0}),
        ownership=_section({"configured": True, "teams": []}),
    )
    html = ars.render_analysis_report_html(data)
    assert "No runs in this window." in html
    assert "nothing to investigate" in html
    assert "No duration data captured" in html
    assert "No release gate decisions in this window." in html
    assert "No open linked defects." in html
    assert "No failures to attribute in this window." in html


# ── Weekly day-bucket math ──────────────────────────────────────────────────


def test_fill_day_buckets_fills_gaps_and_weights_pass_rate():
    start = NOW - timedelta(days=7)
    rows = [
        (start, 3, 90, 100),                       # day 0: 90%
        (start + timedelta(days=2), 1, 40, 80),    # day 2: 50%
        (start + timedelta(days=2, hours=5), 1, 40, 40),  # same day merge → 80/120
    ]
    buckets = ars.fill_day_buckets(rows, start, 7)
    assert len(buckets) == 7
    assert buckets[0] == {"date": start.date().isoformat(), "runs": 3, "pass_rate": 90.0}
    assert buckets[1]["runs"] == 0 and buckets[1]["pass_rate"] is None
    assert buckets[2]["runs"] == 2
    assert buckets[2]["pass_rate"] == pytest.approx(66.7, abs=0.1)
    assert all(b["runs"] == 0 for b in buckets[3:])


def test_fill_day_buckets_empty_rows_yield_all_empty_days():
    start = NOW - timedelta(days=1)
    buckets = ars.fill_day_buckets([], start, 1)
    assert buckets == [{"date": start.date().isoformat(), "runs": 0, "pass_rate": None}]


# ── Filename ────────────────────────────────────────────────────────────────


def test_report_attachment_filename_daily_and_weekly():
    assert ars.report_attachment_filename("acme", NOW, "1d") == (
        "testlookup-report-acme-20260710.html"
    )
    assert ars.report_attachment_filename("acme", NOW, "7d") == (
        "testlookup-report-acme-20260710-weekly.html"
    )


def test_report_attachment_filename_sanitizes_slug():
    name = ars.report_attachment_filename("Acme Corp/QA!", NOW, "1d")
    assert name == "testlookup-report-acme-corp-qa-20260710.html"
    assert ars.report_attachment_filename(None, NOW, "1d") == (
        "testlookup-report-project-20260710.html"
    )


# ── Size bound ──────────────────────────────────────────────────────────────


def test_size_bound_passes_small_documents_through():
    html = ars.render_analysis_report_html(_data())
    assert ars.enforce_size_bound(html, _data()) == html


def test_size_bound_rerenders_with_shrunk_caps():
    data = _data()
    oversized = "x" * (ars.MAX_REPORT_BYTES + 1)
    result = ars.enforce_size_bound(oversized, data)
    assert len(result.encode("utf-8")) <= ars.MAX_REPORT_BYTES
    assert "TestLookup — Analysis Report" in result


def test_size_bound_minimal_fallback_when_shrunk_render_still_too_big():
    data = _data()
    oversized = "x" * (ars.MAX_REPORT_BYTES + 1)
    with patch.object(ars, "render_analysis_report_html", return_value=oversized):
        result = ars.enforce_size_bound(oversized, data)
    assert len(result.encode("utf-8")) <= ars.MAX_REPORT_BYTES
    assert "Open the live report in TestLookup instead" in result


# ── Dashboard parity ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_executive_summary_equals_summary_report_service_output():
    """Parity pin: the report's executive-summary numbers ARE the output of
    ``build_summary_report`` (the /reports/summary source) — used verbatim,
    never re-derived."""
    summary = _summary_payload()
    coverage = {"summary": {"total_executions": 777, "suite_count": 3}}

    class _PriorTotals:
        passed, failed, broken = 100, 10, 0
        evaluated = 110

    with (
        patch(
            "app.services.summary_report_service.build_summary_report",
            new=AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.analytics_service.coverage_stats",
            new=AsyncMock(return_value=coverage),
        ),
        patch(
            "app.services.summary_report_service._window_totals",
            new=AsyncMock(return_value=(_PriorTotals(), 5, 0, None)),
        ),
    ):
        db = MagicMock()
        result = await ars._collect_summary(
            db, uuid.uuid4(), days=1, start=NOW - timedelta(days=1), now=NOW,
        )

    assert result["summary"] is summary  # verbatim, same object
    assert result["coverage_summary"] == coverage["summary"]
    # Prior rate is the same weighted formula the dashboard uses.
    assert result["prior"]["weighted_pass_rate_pct"] == pytest.approx(90.9, abs=0.1)

    # And the renderer surfaces exactly those numbers.
    data = _data(summary=_section({**result, "day_buckets": None}))
    html = ars.render_analysis_report_html(data)
    assert str(summary["totals"]["total_test_cases"]) in html
    assert "777" in html
    assert f"{summary['totals']['weighted_pass_rate_pct']:.1f}%" in html


# ── collect: never-raises ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_records_reason_when_a_section_collector_raises():
    project = MagicMock()
    project.name, project.slug = "Acme", "acme"
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=project))
    )

    async def _boom(*_a, **_k):
        raise RuntimeError("subsystem down")

    async def _ok(*_a, **_k):
        return {"items": [], "total": 0}

    with (
        patch.object(ars, "_collect_summary", _boom),
        patch.object(ars, "_collect_runs", _ok),
        patch.object(ars, "_collect_failures", _boom),
        patch.object(ars, "_collect_flaky", _ok),
        patch.object(ars, "_collect_slowest", _ok),
        patch.object(ars, "_collect_gate", _boom),
        patch.object(ars, "_collect_defects", _ok),
        patch.object(ars, "_collect_ownership", _ok),
    ):
        data = await ars.collect_analysis_report_data(db, uuid.uuid4(), "1d", now=NOW)

    assert data["sections"]["summary"]["ok"] is False
    assert "unavailable" in data["sections"]["summary"]["reason"].lower()
    assert data["sections"]["runs"]["ok"] is True
    # The document still renders.
    html = ars.render_analysis_report_html(data)
    assert "TestLookup — Analysis Report" in html


@pytest.mark.asyncio
async def test_collect_rejects_unknown_window():
    with pytest.raises(ValueError):
        await ars.collect_analysis_report_data(MagicMock(), uuid.uuid4(), "30d")


# ── Digest attachment wrapper: never raises ─────────────────────────────────


@pytest.mark.asyncio
async def test_build_digest_report_attachment_returns_none_on_builder_crash():
    with patch.object(
        ars, "build_analysis_report_html", new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await ars.build_digest_report_attachment(
            MagicMock(), uuid.uuid4(), "weekly", now=NOW,
        )
    assert result is None  # digest itself must still deliver


@pytest.mark.asyncio
async def test_build_digest_report_attachment_none_for_non_project_subscription():
    assert await ars.build_digest_report_attachment(MagicMock(), None, "daily") is None


@pytest.mark.asyncio
async def test_build_digest_report_attachment_returns_filename_and_html():
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="acme"))
    )
    with patch.object(
        ars, "build_analysis_report_html", new=AsyncMock(return_value="<html>r</html>"),
    ):
        result = await ars.build_digest_report_attachment(db, uuid.uuid4(), "weekly", now=NOW)
    assert result == ("testlookup-report-acme-20260710-weekly.html", "<html>r</html>")


@pytest.mark.asyncio
async def test_build_digest_report_attachment_daily_maps_to_1d_window():
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="acme"))
    )
    build = AsyncMock(return_value="<html/>")
    with patch.object(ars, "build_analysis_report_html", new=build):
        await ars.build_digest_report_attachment(db, uuid.uuid4(), "daily", now=NOW)
    assert build.await_args.args[2] == "1d"


# ── MIME assembly ───────────────────────────────────────────────────────────

_SMTP_CFG = {
    "enabled": True, "host": "h", "port": 587, "user": "", "password": "",
    "tls": False, "from_address": "noreply@x.com",
}


@pytest.mark.asyncio
async def test_send_with_attachments_builds_multipart_mixed():
    from app.services.notification import email_service

    send = AsyncMock()
    with patch.object(email_service, "aiosmtplib") as smtp:
        smtp.send = send
        await email_service.send_html_email_with_attachments(
            to_email="qa@x.com",
            subject="Digest",
            html_body="<p>digest body</p>",
            attachments=[
                ("testlookup-report-acme-20260710-weekly.html", "<html>report</html>", "text/html"),
            ],
            smtp_cfg=_SMTP_CFG,
        )
    send.assert_awaited_once()
    msg = send.await_args.args[0]
    assert msg.get_content_type() == "multipart/mixed"
    parts = msg.get_payload()
    assert parts[0].get_content_type() == "multipart/alternative"
    body_types = [p.get_content_type() for p in parts[0].get_payload()]
    assert body_types == ["text/plain", "text/html"]
    html_body = parts[0].get_payload()[1].get_payload(decode=True).decode("utf-8")
    assert html_body == "<p>digest body</p>"
    attachment = parts[1]
    assert attachment.get_content_type() == "text/html"
    assert attachment.get_filename() == "testlookup-report-acme-20260710-weekly.html"
    assert "attachment" in attachment.get("Content-Disposition", "")
    assert attachment.get_payload(decode=True).decode("utf-8") == "<html>report</html>"


@pytest.mark.asyncio
async def test_send_with_no_attachments_delegates_to_plain_html_email():
    from app.services.notification import email_service

    send = AsyncMock()
    with patch.object(email_service, "aiosmtplib") as smtp:
        smtp.send = send
        await email_service.send_html_email_with_attachments(
            to_email="qa@x.com", subject="Digest", html_body="<p>hi</p>",
            attachments=None, smtp_cfg=_SMTP_CFG,
        )
    msg = send.await_args.args[0]
    # Same MIME shape as send_html_email — byte-identical digest path.
    assert msg.get_content_type() == "multipart/alternative"


@pytest.mark.asyncio
async def test_send_with_attachments_noops_when_smtp_disabled():
    from app.services.notification import email_service

    send = AsyncMock()
    with patch.object(email_service, "aiosmtplib") as smtp:
        smtp.send = send
        await email_service.send_html_email_with_attachments(
            to_email="qa@x.com", subject="s", html_body="<p/>",
            attachments=[("f.html", "<html/>", "text/html")],
            smtp_cfg={"enabled": False},
        )
    send.assert_not_awaited()


# ── Digest note helpers ─────────────────────────────────────────────────────


def test_slack_text_gains_attachment_note_only_when_enabled():
    from app.services.digest_content_service import (
        REPORT_ATTACHMENT_NOTE,
        digest_text_with_attachment_note,
    )
    assert digest_text_with_attachment_note("body", False) == "body"
    with_note = digest_text_with_attachment_note("body", True)
    assert with_note == f"body\n{REPORT_ATTACHMENT_NOTE}"


def test_apologetic_note_lands_before_body_close():
    from app.services.digest_content_service import (
        REPORT_BUILD_FAILED_NOTE,
        append_digest_html_note,
    )
    html = "<html><body><p>digest</p></body></html>"
    out = append_digest_html_note(html, REPORT_BUILD_FAILED_NOTE)
    assert REPORT_BUILD_FAILED_NOTE in out
    assert out.index(REPORT_BUILD_FAILED_NOTE) < out.index("</body>")
    # Marker-less fallback still carries the note.
    assert REPORT_BUILD_FAILED_NOTE in append_digest_html_note("<p>x</p>", REPORT_BUILD_FAILED_NOTE)


# ── Subscription schema ─────────────────────────────────────────────────────


def test_subscription_create_defaults_report_attachment_off():
    from app.models.schemas import DigestSubscriptionCreate
    sub = DigestSubscriptionCreate(name="daily digest")
    assert sub.report_attachment is False


def test_subscription_update_accepts_report_attachment():
    from app.models.schemas import DigestSubscriptionUpdate
    upd = DigestSubscriptionUpdate(report_attachment=True)
    assert upd.model_dump(exclude_unset=True) == {"report_attachment": True}


def test_subscription_response_carries_report_attachment_default_off():
    from app.models.schemas import DigestSubscriptionResponse
    fields = DigestSubscriptionResponse.model_fields
    assert "report_attachment" in fields
    assert fields["report_attachment"].default is False


def test_orm_model_defaults_report_attachment_off():
    from app.models.postgres import DigestSubscription
    col = DigestSubscription.__table__.c.report_attachment
    assert col.nullable is False
    assert col.default.arg is False


# ── Migration 0107 chain sanity ─────────────────────────────────────────────


def test_migration_0107_chains_from_0106_with_downgrade():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "versions" / "0107_digest_report_attachment.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0107", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0107"
    assert mod.down_revision == "0106"
    assert callable(mod.upgrade) and callable(mod.downgrade)


# ── Endpoint ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_returns_html_attachment():
    from app.routers import analysis_report as router_mod

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value="acme"))
    )
    with patch.object(
        router_mod, "build_analysis_report_html",
        new=AsyncMock(return_value="<html>report</html>"),
    ):
        response = await router_mod.download_analysis_report(
            project_id=uuid.uuid4(), window="7d", db=db, _role=None, _=None,
        )
    assert response.media_type.startswith("text/html")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="testlookup-report-acme-')
    assert disposition.endswith('-weekly.html"')
    assert response.body == b"<html>report</html>"


def test_endpoint_route_is_project_guarded():
    """Authorization ratchet contract: the {project_id} route depends on
    require_project_access (and a QA_ENGINEER+ role floor)."""
    import inspect

    from app.routers import analysis_report as router_mod

    src = inspect.getsource(router_mod)
    assert "require_project_access" in src
    assert "UserRole.QA_ENGINEER" in src
    route = next(
        r for r in router_mod.router.routes
        if getattr(r, "path", "") == "/api/v1/projects/{project_id}/reports/analysis"
    )
    assert "GET" in route.methods
