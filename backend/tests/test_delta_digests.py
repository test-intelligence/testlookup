"""Delta digests (PMF US-7.4).

Covers:

* ``is_zero_change`` — the zero-change window predicate matrix.
* ``zero_change_line`` — the one-liner formatting.
* ``render_digest_text`` — Slack rendering: delta-first, totals secondary,
  zero-change one-liner collapse.
* ``render_digest_html`` — the "Since last digest" block (counts, top-3,
  quarantine debt, gate change), the zero-change one-liner page, and XSS
  escaping of delta test names.
* Digest subscription schema — ``send_when_unchanged`` create default /
  partial update / response mapping.
* Migration 0106 chain sanity (0105 → 0106, downgrade implemented).
* ``send_html_email`` exists (the scheduled dispatcher previously imported
  a non-existent ``send_email`` — regression guard) and no-ops when SMTP
  is disabled.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

if "aiosmtplib" not in sys.modules:
    _stub = types.ModuleType("aiosmtplib")
    _stub.send = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["aiosmtplib"] = _stub

from app.services import digest_content_service as dcs  # noqa: E402


def _delta(**overrides) -> dict:
    base = {
        "window_start": "2026-07-09T07:00:00+00:00",
        "window_end": "2026-07-10T07:00:00+00:00",
        "new_failures": 0,
        "new_failures_top": [],
        "newly_flaky": 0,
        "recovered": 0,
        "quarantine_debt": {"active": 0, "stale": 0, "ready_to_promote": 0},
        "gate_change": None,
    }
    base.update(overrides)
    return base


def _digest(**overrides) -> dict:
    base = {
        "project_name": "acme",
        "period": "daily",
        "generated_at": "2026-07-10T07:00:00+00:00",
        "total_runs": 12,
        "avg_pass_rate": 96.5,
        "pass_rate_trend": 1.2,
        "new_regressions": 0,
        "top_blockers": [],
        "top_clusters": [],
        "flaky_test_count": 3,
        "release_decisions": [],
        "action_items": ["Quality metrics are stable — no urgent action items"],
        "latest_run_total_tests": 480,
    }
    base.update(overrides)
    return base


# ── Zero-change predicate ───────────────────────────────────────────────────


def test_all_quiet_is_zero_change():
    assert dcs.is_zero_change(_delta()) is True


@pytest.mark.parametrize("changed", [
    {"new_failures": 1},
    {"newly_flaky": 2},
    {"recovered": 1},
    {"gate_change": {"from": "GO", "to": "NO_GO"}},
])
def test_any_transition_breaks_zero_change(changed):
    assert dcs.is_zero_change(_delta(**changed)) is False


def test_quarantine_debt_alone_is_still_zero_change():
    # Debt is a standing stock, not a change — it must not force a full
    # digest every day.
    delta = _delta(quarantine_debt={"active": 7, "stale": 2, "ready_to_promote": 1})
    assert dcs.is_zero_change(delta) is True


# ── One-liner ───────────────────────────────────────────────────────────────


def test_zero_change_line_includes_tests_and_pass_rate():
    line = dcs.zero_change_line(_digest())
    assert line == "No changes since the last digest — 480 tests, pass rate 96.5%."


def test_zero_change_line_degrades_without_numbers():
    line = dcs.zero_change_line(
        _digest(latest_run_total_tests=None, avg_pass_rate=None)
    )
    assert line == "No changes since the last digest."


# ── Slack text rendering ────────────────────────────────────────────────────


def test_text_rendering_is_delta_first_with_totals_secondary():
    digest = _digest(
        delta=_delta(
            new_failures=4,
            new_failures_top=["test_login", "test_checkout", "test_search"],
            newly_flaky=2,
            recovered=1,
            quarantine_debt={"active": 5, "stale": 2, "ready_to_promote": 1},
            gate_change={"from": "GO", "to": "CONDITIONAL_GO"},
        ),
        is_zero_change=False,
        changes_since="2026-07-09T07:00:00+00:00",
    )
    text = dcs.render_digest_text(digest)
    lines = text.splitlines()
    assert lines[0] == "Since last digest:"
    assert "New failures: 4 (top: test_login, test_checkout, test_search)" in lines[1]
    assert "Newly flaky: 2" in lines[1]
    assert "Fixed: 1" in lines[1]
    assert "Quarantine debt: 5 active (2 stale, 1 ready to promote)" in lines[2]
    assert "Gate verdict changed: GO → CONDITIONAL_GO" in lines[3]
    # Absolute totals are still present, AFTER the delta block.
    totals_idx = next(i for i, ln in enumerate(lines) if ln.startswith("Totals:"))
    assert totals_idx > 3
    assert "12 runs" in lines[totals_idx]
    assert "pass rate 96.5% (+1.2%)" in lines[totals_idx]


def test_text_rendering_zero_change_collapses_to_one_liner():
    digest = _digest(delta=_delta(), is_zero_change=True)
    assert dcs.render_digest_text(digest) == dcs.zero_change_line(digest)


def test_text_rendering_without_delta_keeps_totals_only():
    text = dcs.render_digest_text(_digest())
    assert "Since last digest" not in text
    assert text.startswith("Totals: 12 runs")


# ── HTML rendering ──────────────────────────────────────────────────────────


def test_html_includes_delta_block_and_top_failures():
    digest = _digest(
        delta=_delta(
            new_failures=2,
            new_failures_top=["test_a", "test_b"],
            newly_flaky=1,
            recovered=3,
            quarantine_debt={"active": 4, "stale": 1, "ready_to_promote": 2},
            gate_change={"from": "GO", "to": "NO_GO"},
        ),
        is_zero_change=False,
    )
    html = dcs.render_digest_html(digest)
    assert "Since last digest" in html
    assert "test_a" in html and "test_b" in html
    assert "4</strong> active" in html
    assert "1 stale, 2 ready to promote" in html
    assert "Gate verdict changed" in html
    # Absolute totals remain as the secondary block.
    assert "Avg Pass Rate" in html


def test_html_zero_change_renders_one_liner_only():
    digest = _digest(delta=_delta(), is_zero_change=True)
    html = dcs.render_digest_html(digest)
    assert "No changes since the last digest — 480 tests, pass rate 96.5%." in html
    # None of the full-digest sections render.
    assert "Avg Pass Rate" not in html
    assert "Action Items" not in html


def test_html_escapes_delta_test_names():
    digest = _digest(
        delta=_delta(
            new_failures=1,
            new_failures_top=["<script>alert(1)</script>"],
        ),
        is_zero_change=False,
    )
    html = dcs.render_digest_html(digest)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_without_delta_is_unchanged_legacy_shape():
    html = dcs.render_digest_html(_digest())
    assert "Since last digest" not in html
    assert "Avg Pass Rate" in html


# ── Subscription schema: send_when_unchanged ────────────────────────────────


def test_subscription_create_defaults_send_when_unchanged_true():
    from app.models.schemas import DigestSubscriptionCreate
    sub = DigestSubscriptionCreate(name="daily digest")
    assert sub.send_when_unchanged is True


def test_subscription_update_accepts_send_when_unchanged():
    from app.models.schemas import DigestSubscriptionUpdate
    upd = DigestSubscriptionUpdate(send_when_unchanged=False)
    assert upd.model_dump(exclude_unset=True) == {"send_when_unchanged": False}


def test_subscription_response_carries_send_when_unchanged():
    from app.models.schemas import DigestSubscriptionResponse
    fields = DigestSubscriptionResponse.model_fields
    assert "send_when_unchanged" in fields
    assert fields["send_when_unchanged"].default is True


def test_digest_content_response_carries_delta_fields():
    from app.models.schemas import DigestContentResponse
    resp = DigestContentResponse(period="daily", generated_at="x")
    assert resp.delta is None
    assert resp.is_zero_change is None
    assert resp.changes_since is None


# ── Migration 0106 chain sanity ─────────────────────────────────────────────


def test_migration_0106_chains_from_0105_with_downgrade():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "versions" / "0106_notify_routing_delta_digests.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0106", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0106"
    assert mod.down_revision == "0105"
    assert callable(mod.upgrade) and callable(mod.downgrade)


# ── send_html_email regression guard ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_html_email_exists_and_noops_when_smtp_disabled():
    # The scheduled digest dispatcher used to import a non-existent
    # ``send_email`` from this module, so every digest email failed.
    from app.services.notification import email_service
    send = AsyncMock()
    with patch.object(sys.modules["aiosmtplib"], "send", send):
        await email_service.send_html_email(
            to_email="u@x.com", subject="s", html_body="<p>hi</p>",
            smtp_cfg={"enabled": False},
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_html_email_sends_when_enabled():
    from app.services.notification import email_service
    send = AsyncMock()
    with patch.object(email_service, "aiosmtplib") as smtp:
        smtp.send = send
        await email_service.send_html_email(
            to_email="u@x.com", subject="Digest", html_body="<p>hi</p>",
            smtp_cfg={
                "enabled": True, "host": "h", "port": 587,
                "user": "", "password": "", "tls": False,
                "from_address": "noreply@x.com",
            },
        )
    send.assert_awaited_once()
    msg = send.await_args.args[0]
    assert msg["Subject"] == "Digest"
    assert msg["To"] == "u@x.com"
