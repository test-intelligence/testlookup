"""Run emails carry the whole run, so a reader need not open the dashboard.

Owner request 2026-10-10: a run email said one line ("2 failures detected in
E2E Lab (build #42)") plus four numbers. These tests pin what the email now
carries -- the run's facts, results against the previous build, the release
gate, the AI conclusions the review gate allows, every failing test with its
error and root cause, the suites and the links -- and the rules around it: the
report is escaped, a refused review gate withholds every AI conclusion, the
report never lands on the notification row, and a report that cannot be
built or rendered never stops the short email.

The SQL that gathers the report is covered against PostgreSQL in
``tests/integration/test_run_email_report_postgres.py``.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notification import email_service
from app.services.notification import run_report as rr

RUN = "11111111-1111-1111-1111-111111111111"


def _report(**overrides):
    report = {
        "project": {"id": "p1", "name": "Checkout Web"},
        "run": {
            "id": RUN, "build_number": "142", "status": "FAILED", "suite": "Checkout",
            "branch": "main", "commit": "a1b2c3d4e5f6", "environment": "staging",
            "framework": None, "trigger": "ci", "source": "upload",
            "ci_provider": "github_actions", "ci_repo": "acme/shop", "pr_number": 42,
            "ci_actor": "sara", "ci_run_url": "https://ci.example.com/run/1", "jenkins_job": None,
            "started_at": "2026-10-10T08:00:00+00:00", "finished_at": "2026-10-10T08:04:10+00:00",
            "duration_ms": 250_000, "ingestion_complete": True, "rejected_results": 0,
        },
        "counts": {"total": 6, "passed": 3, "failed": 2, "broken": 1, "skipped": 0, "pass_rate": 50.0},
        "links": {
            "run": f"https://tl.example.com/runs/{RUN}",
            "failures": f"https://tl.example.com/runs/{RUN}?status=FAILED",
            "analysis": f"https://tl.example.com/runs/{RUN}?tab=analysis",
            "release_gate": f"https://tl.example.com/release-gate/{RUN}",
        },
        "previous": {"build_number": "141", "pass_rate": 80.0, "failed": 1, "link": "https://tl.example.com/runs/prev"},
        "suites": [
            {"name": "Checkout", "total": 2, "passed": 0, "failed": 2, "broken": 0, "skipped": 0},
            {"name": "Payments", "total": 4, "passed": 3, "failed": 0, "broken": 1, "skipped": 0},
        ],
        "failures": [
            {"id": "c1", "name": "test_pay_<script>", "suite": "Checkout", "class_name": "PayTest",
             "status": "FAILED", "duration_ms": 1200, "error": "AssertionError: <b>total</b> != 40",
             "category": "PRODUCT_BUG", "is_new": True, "quarantined": False, "flaky": False,
             "assignee": "sara", "link": "https://tl.example.com/runs/r/tests/c1",
             "ai_root_cause": "Tax is applied twice", "ai_confidence": 82},
            {"id": "c2", "name": "test_cart", "suite": "Checkout", "class_name": None,
             "status": "BROKEN", "duration_ms": None, "error": None, "category": None,
             "is_new": False, "quarantined": True, "flaky": True, "assignee": None,
             "link": "https://tl.example.com/runs/r/tests/c2"},
        ],
        "failing_total": 30,
        "categories": {"PRODUCT_BUG": 1, "UNKNOWN": 1},
        "new_failures": 1,
        "ai_allowed": True,
        "ai_watermark": None,
        "ai_summary": {
            "headline": "Checkout totals regressed", "summary": "Two checkout tests fail on totals.",
            "takeaways": ["Tax doubled"], "actions": ["Revert the tax change"], "signal": "RED",
            "risk_score": 71, "dominant_failure": "Totals mismatch", "generated_at": None,
        },
        "release": {
            "name": "R-2026.10", "version": None, "status": "in_progress",
            "link": "https://tl.example.com/releases/rel", "verdict": "NO_GO", "live": True,
            "decided_at": None, "blocking_count": 12, "blocking_from_this_run": 2,
            "blocking_reasons": ["test_pay (Checkout) is FAILED", "CRITICAL defect open: Totals"],
            "pass_rate": 91.5, "distinct_tests": 120, "evidence_count": 118, "run_count": 7,
            "measured": True, "quarantined_failures": 1, "insufficient_reason": None,
            "open_defects": 3, "blocking_defects": 1,
        },
    }
    report.update(overrides)
    return report


# ── Rendering ────────────────────────────────────────────────────────────────


def test_the_html_report_carries_everything_a_reader_acts_on():
    html = rr.render_run_report_html(_report())

    for expected in (
        "Checkout Web", "#142", "Checkout", "main", "a1b2c3d4e5f6", "staging",
        "github_actions · acme/shop · PR #42", "ci · sara", "4m 10s",
        "Pass rate 30.0 pts down vs build #141 (80.0%, 1 failing)",
        "R-2026.10", "NO GO", "live preview including this run",
        "12 blocking", "2 of them failing in this run", "1 quarantined failure(s) set aside",
        "3 open defect(s), 1 blocking", "test_pay (Checkout) is FAILED", "…and 10 more",
        "Checkout totals regressed", "RED · risk 71/100", "Tax doubled", "Revert the tax change",
        "Failing tests (30) · 1 new since the previous build", "NEW", "still failing",
        "quarantined", "flaky", "product bug", "owner sara",
        "AI root cause (82% confidence)", "Tax is applied twice",
        "…and 28 more failing test(s) in the run.", "By test suite", "Payments",
        "https://ci.example.com/run/1", "Previous build #141", "/release-gate/",
    ):
        assert expected in html, expected


def test_the_html_report_escapes_test_names_and_errors():
    html = rr.render_run_report_html(_report())
    assert "<script>" not in html and "test_pay_&lt;script&gt;" in html
    assert "<b>total</b>" not in html and "&lt;b&gt;total&lt;/b&gt;" in html


def test_a_refused_gate_shows_the_notice_and_no_ai_summary():
    report = _report(ai_allowed=False, ai_withheld=rr.AI_WITHHELD_NOTICE)
    report.pop("ai_summary")
    html = rr.render_run_report_html(report)
    text = rr.render_run_report_text(report)
    for out in (html, text):
        assert "awaiting human review" in out
        assert "Checkout totals regressed" not in out


def test_a_draft_summary_carries_its_watermark():
    html = rr.render_run_report_html(_report(ai_watermark="DRAFT — not yet reviewed"))
    assert "DRAFT — not yet reviewed" in html


def test_the_ai_summary_can_be_left_to_the_message_above():
    html = rr.render_run_report_html(_report(), include_ai_summary=False)
    text = rr.render_run_report_text(_report(), include_ai_summary=False)
    for out in (html, text):
        assert "Checkout totals regressed" not in out
        assert "Tax is applied twice" in out  # per-test root causes stay


def test_the_text_report_mirrors_the_html():
    text = rr.render_run_report_text(_report())
    for expected in (
        "RESULTS", "pass rate 50.0%", "RUN DETAILS", "Project: Checkout Web", "Test suite: Checkout",
        "CI job: https://ci.example.com/run/1", "RELEASE IMPACT", "R-2026.10 gate: NO GO",
        "12 blocking (2 failing in this run)", "  - test_pay (Checkout) is FAILED",
        "AI SUMMARY", "Checkout totals regressed", "FAILING TESTS (30)",
        "- test_pay_<script> [FAILED, NEW, product_bug] — Checkout",
        "AI root cause: Tax is applied twice", "…and 28 more failing test(s).",
        "BY TEST SUITE", "- Payments: 4 total", f"Open the run: https://tl.example.com/runs/{RUN}",
    ):
        assert expected in text, expected


def test_an_unevaluated_release_says_so():
    html = rr.render_run_report_html(_report(release={"name": "R-1", "status": "planning", "link": None}))
    assert "the gate has not been evaluated yet" in html


def test_a_minimal_report_renders_without_optional_sections():
    report = _report(previous=None, release=None, ai_summary=None, failures=[], suites=[], categories={})
    html = rr.render_run_report_html(report)
    assert "Results" in html and "Release impact" not in html and "Failing tests (" not in html
    assert rr.render_run_report_html({}) == "" and rr.render_run_report_text({}) == ""


def test_a_non_http_ci_link_is_dropped():
    run = SimpleNamespace(
        id=uuid.uuid4(), build_number="1", status="FAILED", primary_suite_name=None, branch=None,
        commit_hash=None, environment=None, trigger_source=None, ingestion_source=None,
        ci_provider=None, ci_repo=None, pr_number=None, ci_actor=None,
        ci_run_url="javascript:alert(1)", jenkins_job=None, start_time=None, created_at=None,
        end_time=None, duration_ms=None, ingestion_complete=None, ingestion_rejected_tests=None,
    )
    assert rr._run_facts(run)["ci_run_url"] is None


# ── Release reasons ──────────────────────────────────────────────────────────


def test_a_gate_reason_splits_into_suite_fingerprint_and_status():
    assert rr._reason_key("Checkout::abc123 is FAILED") == ("Checkout", "abc123", "FAILED")
    assert rr._reason_key("::abc123 is BROKEN") == ("", "abc123", "BROKEN")
    assert rr._reason_key("CRITICAL defect open: Totals") == (None, None, "CRITICAL defect open: Totals")


@pytest.mark.asyncio
async def test_gate_reasons_name_the_test_and_pass_others_through():
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [("abc123", "test_pay")]))
    out = await rr._readable_reasons(
        db, "p1", ["Checkout::abc123 is FAILED", "Checkout::zzz is BROKEN", "CRITICAL defect open: Totals"]
    )
    assert out == ["test_pay (Checkout) is FAILED", "Checkout::zzz is BROKEN", "CRITICAL defect open: Totals"]


# ── The email around it ──────────────────────────────────────────────────────


def _meta(**extra):
    return {"project_name": "Checkout Web", "build_number": "142", "pass_rate": 50.0,
            "failed_tests": 3, "dashboard_url": "https://tl.example.com/runs/x", **extra}


def test_a_run_email_carries_the_report_in_both_parts():
    meta = _meta(run_report=_report())
    html = email_service._build_html("Run failed", "3 failures", "run_failed", meta)
    plain = email_service._build_plain("Run failed", "3 failures", meta, "run_failed")
    assert "Tax is applied twice" in html and "Release impact" in html
    assert "RELEASE IMPACT" in plain and "Tax is applied twice" in plain
    assert "Checkout totals regressed" in html and "Checkout totals regressed" in plain


def test_an_email_without_a_report_is_unchanged():
    meta = _meta()
    html = email_service._build_html("Run failed", "3 failures", "run_failed", meta)
    plain = email_service._build_plain("Run failed", "3 failures", meta, "run_failed")
    assert "Release impact" not in html and "RELEASE IMPACT" not in plain
    assert "Failed tests: 3" in plain


def test_the_ai_summary_email_does_not_repeat_its_summary():
    meta = _meta(run_report=_report(), executive_panel={"headline": "Panel headline"})
    plain = email_service._build_plain("AI summary", "body", meta, "ai_analysis_complete")
    assert "Checkout totals regressed" not in plain
    assert "FAILING TESTS (30)" in plain


def test_a_report_that_cannot_render_leaves_the_short_email(monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("bad report")

    monkeypatch.setattr(rr, "render_run_report_html", boom)
    monkeypatch.setattr(rr, "render_run_report_text", boom)
    meta = _meta(run_report=_report())
    html = email_service._build_html("Run failed", "3 failures", "run_failed", meta)
    plain = email_service._build_plain("Run failed", "3 failures", meta, "run_failed")
    assert "3 failures" in html and "3 failures" in plain


# ── The relay builds it once per run ────────────────────────────────────────


def _savepoint_db():
    db = MagicMock()
    db.savepoints = 0

    @asynccontextmanager
    async def begin_nested():
        db.savepoints += 1
        yield

    db.begin_nested = begin_nested
    return db


@pytest.mark.asyncio
async def test_the_relay_builds_one_report_per_run_and_survives_a_failure(monkeypatch):
    from app.services.notification import manager

    runs = [uuid.uuid4() for _ in range(3)]
    rows = [SimpleNamespace(run_id=runs[0]), SimpleNamespace(run_id=runs[0]),
            SimpleNamespace(run_id=None), SimpleNamespace(run_id=runs[1]), SimpleNamespace(run_id=runs[2])]
    built = []

    async def fake_build(db, run_id, *, base_url, channel):
        built.append(run_id)
        if run_id == runs[1]:
            raise RuntimeError("read failed")
        return {"project": {"id": "p1"}}, SimpleNamespace(audit_action=None)

    monkeypatch.setattr(rr, "build_run_report", fake_build)
    db = _savepoint_db()
    reports, decisions = await manager._build_run_reports(db, rows)

    assert built == runs                       # one build per distinct run
    assert set(reports) == {str(runs[0]), str(runs[2])}
    assert [d[1] for d in decisions] == [runs[0], runs[2]]
    assert db.savepoints == 3                   # each read under its own savepoint


@pytest.mark.asyncio
async def test_the_relay_caps_the_reports_one_batch_builds(monkeypatch):
    from app.services.notification import manager

    rows = [SimpleNamespace(run_id=uuid.uuid4()) for _ in range(manager._MAX_RUN_REPORTS_PER_BATCH + 5)]
    build = AsyncMock(return_value=({"project": {"id": "p1"}}, None))
    monkeypatch.setattr(rr, "build_run_report", build)
    reports, _ = await manager._build_run_reports(_savepoint_db(), rows)
    assert build.await_count == manager._MAX_RUN_REPORTS_PER_BATCH == len(reports)


@pytest.mark.asyncio
async def test_an_email_route_receives_the_report_and_the_row_does_not(monkeypatch):
    from app.models.postgres import NotificationChannel, NotificationEventType
    from app.services.notification import manager

    run_id = uuid.uuid4()
    row_metadata = {
        "build_number": "142",
        manager._TEAM_ROUTE_METADATA_KEY: {
            "team_name": "payments",
            "channel_type": NotificationChannel.EMAIL.value,
            "target": "team@example.com",
        },
    }
    row = SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=run_id, preference_id=None,
        delivery_attempts=1, delivery_token=uuid.uuid4(),
        event_type=NotificationEventType.RUN_FAILED.value, title="Run failed", body="3 failures",
        delivery_metadata=row_metadata, delivery_key="k" * 64,
    )
    claim_db = MagicMock()
    claim_db.commit = AsyncMock()
    outcome_db = MagicMock()
    outcome_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    outcome_db.commit = AsyncMock()
    sessions = iter([claim_db, outcome_db])

    class _SessionContext:
        async def __aenter__(self):
            return next(sessions)

        async def __aexit__(self, *_args):
            return False

    report = {"project": {"id": str(row.project_id)}, "counts": {"total": 6}}
    decision = SimpleNamespace(audit_action="distributed_unreviewed")
    monkeypatch.setattr(manager, "AsyncSessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(manager, "claim_pending_notification_deliveries", AsyncMock(return_value=[row]))
    monkeypatch.setattr(manager, "_build_run_reports",
                        AsyncMock(return_value=({str(run_id): report}, [(decision, run_id, str(row.project_id))])))
    monkeypatch.setattr(manager.email_service, "_get_smtp_cfg", AsyncMock(return_value={"enabled": True}))
    send = AsyncMock()
    monkeypatch.setattr(manager.email_service, "send_notification", send)
    recorded = AsyncMock()
    monkeypatch.setattr("app.services.report_distribution_policy.record_distribution_detached", recorded)

    result = await manager.relay_pending_notification_deliveries()

    assert result["sent"] == 1, result
    assert send.await_args.kwargs["metadata"]["run_report"] is report
    assert "run_report" not in row.delivery_metadata
    recorded.assert_awaited_once()
    assert recorded.await_args.kwargs["channel"] == manager._RUN_REPORT_CHANNEL
