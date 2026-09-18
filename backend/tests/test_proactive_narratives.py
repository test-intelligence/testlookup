"""Proactive narrative intelligence (Agentic plan AI-7).

Covers the three surfaces that carry the Investigator's persisted verdicts
to humans, plus the honesty guarantees:

* **Attached analysis report** — the new "Agent investigations" section:
  present with fixture verdicts (cause badge, confidence + basis,
  hypothesis tally, cockpit deep link, quoted narrative excerpt), capped at
  5 with an overflow line, omitted with the standard explanatory line when
  none exist, hostile narrative text escaped, AI/shadow label pinned.
* **Digest delta line** — ``investigations`` in ``compute_digest_deltas``'s
  shape, ``investigator_delta_line`` formatting, present/absent in the
  text + HTML renderings (zero-investigation windows add NOTHING), and the
  zero-change predicate ignoring investigations.
* **Weekly flaky-debt review** — ``flaky_debt_review``: next-step priority
  matrix, per-team grouping with multi-bucket membership, empty-team
  omission, the automation label, bucket caps, the digest fold-in for
  teams WITHOUT a US-7.3 channel (channel-routed teams excluded), and the
  team-channel delivery loop.
* **No-new-LLM-calls guard** — the report/digest/review paths never import
  or construct an LLM (source tripwire + runtime ``get_llm`` explosion
  monkeypatch, mirroring the Investigator's offline test).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from app.services import analysis_report_service as ars  # noqa: E402
from app.services import digest_content_service as dcs  # noqa: E402
from app.services import flaky_debt_review as fdr  # noqa: E402

NOW = datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)
HOSTILE = "<script>alert(1)</script>"

RUN_ID = str(uuid.UUID(int=7))


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _inv_item(i: int = 1, **overrides) -> dict:
    base = {
        "investigation_id": str(uuid.UUID(int=100 + i)),
        "run_id": RUN_ID,
        "run_build": f"b{i}",
        "mode": "shadow",
        "primary_cause": "infra",
        "confidence": 78,
        "confidence_basis": "heuristic_estimate",
        "narrative_excerpt": "Primary cause: infra — 12 failures share connection-refused signatures.",
        "hypothesis_tally": {"validated": 2, "invalidated": 2, "inconclusive": 1},
        "completed_at": NOW.isoformat(),
    }
    base.update(overrides)
    return base


def _report_data(items: list[dict] | None, total: int | None = None, ok: bool = True) -> dict:
    if items is None:
        section = {"ok": False, "reason": "Agent investigation data unavailable.", "data": None}
    else:
        section = {"ok": ok, "reason": None, "data": {"items": items, "total": total if total is not None else len(items)}}
    return {
        "project": {"id": "pid", "name": "Acme", "slug": "acme"},
        "window": {
            "key": "7d", "days": 7,
            "start": SINCE.isoformat(), "end": NOW.isoformat(),
        },
        "generated_at": NOW.isoformat(),
        "base_url": "http://dash.local",
        "caps": ars.DEFAULT_CAPS,
        "sections": {"investigations": section},
    }


def _delta(**overrides) -> dict:
    base = {
        "window_start": SINCE.isoformat(),
        "window_end": NOW.isoformat(),
        "new_failures": 0,
        "new_failures_top": [],
        "newly_flaky": 0,
        "recovered": 0,
        "quarantine_debt": {"active": 0, "stale": 0, "ready_to_promote": 0},
        "gate_change": None,
        "investigations": {"completed": 0, "top": []},
    }
    base.update(overrides)
    return base


def _digest(**overrides) -> dict:
    base = {
        "project_name": "acme",
        "period": "weekly",
        "generated_at": NOW.isoformat(),
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


def _debt_entry(**overrides) -> dict:
    base = {
        "test_name": "test_checkout",
        "test_fingerprint": "fp1",
        "suite_name": "payments-suite",
        "status": "QUARANTINED",
        "team": "payments",
        "owner": "alice@x.com",
        "active": True,
        "stale": False,
        "ready_to_promote": False,
        "newly_flaky": False,
        "consecutive_passes": 0,
        "stale_at": None,
        "detected_at": SINCE.isoformat(),
        "flip_rate": 0.3,
        "next_step": "Review reason — confirm the quarantine rationale still holds: http://dash.local/quarantine",
    }
    base.update(overrides)
    return base


# ═══ 1. Attached report — "Agent investigations" section ════════════════════


def test_report_section_present_with_label_badge_tally_and_deep_link():
    html = ars.render_analysis_report_html(_report_data([_inv_item()]))
    assert "Agent investigations" in html
    # Trust doctrine: explicit AI + shadow labeling.
    assert "shadow mode" in html
    assert "AI-generated by the Investigator agent" in html
    assert "quoted verbatim from the stored investigation verdicts" in html
    # Cause badge uses the shared display vocabulary.
    assert "infrastructure" in html
    # Confidence + basis.
    assert "78%" in html
    assert "heuristic estimate" in html
    # Hypothesis tally.
    assert "2 validated / 2 invalidated / 1 inconclusive" in html
    # Cockpit deep link.
    assert f'href="http://dash.local/deep-investigate/{RUN_ID}"' in html
    # Quoted narrative with source-honesty note.
    assert "connection-refused signatures" in html
    assert "quoted from the stored verdict" in html


def test_report_section_caps_at_five_with_overflow_line():
    items = [_inv_item(i) for i in range(1, 6)]
    html = ars.render_analysis_report_html(_report_data(items, total=8))
    assert "#b5" in html
    assert "+3 more" in html
    assert "open dashboard" in html


def test_report_section_omitted_with_explanatory_line_when_none():
    html = ars.render_analysis_report_html(_report_data([], total=0))
    assert "Agent investigations" in html
    assert "No completed agent investigations in this window." in html
    assert "shadow mode" not in html  # no label without content


def test_report_section_failure_renders_reason_line():
    html = ars.render_analysis_report_html(_report_data(None))
    assert "Agent investigation data unavailable." in html


def test_report_hostile_narrative_and_build_are_escaped():
    item = _inv_item(
        narrative_excerpt=f"Cause found {HOSTILE} in <b>logs</b>.",
        run_build=f"b<img src=x>{HOSTILE}",
    )
    html = ars.render_analysis_report_html(_report_data([item]))
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)" in html
    assert "<img src=x>" not in html
    assert "<b>logs</b>" not in html


def test_narrative_excerpt_clips_to_two_sentences_and_max_len():
    text = "First sentence. Second sentence. Third sentence never shows."
    assert ars.narrative_excerpt(text) == "First sentence. Second sentence."
    assert ars.narrative_excerpt(None) == ""
    assert ars.narrative_excerpt("   ") == ""
    long = "x" * 500
    clipped = ars.narrative_excerpt(long)
    assert len(clipped) <= 300 and clipped.endswith("…")
    # Whitespace normalized (stored narratives may carry newlines).
    assert ars.narrative_excerpt("a\n b.  c") == "a b. c"


def test_hypothesis_tally_counts_only_terminal_statuses():
    hypotheses = [
        {"status": "validated"}, {"status": "validated"},
        {"status": "invalidated"}, {"status": "inconclusive"},
        {"status": "pending"}, {"status": "running"}, {},
    ]
    assert ars.hypothesis_tally(hypotheses) == {
        "validated": 2, "invalidated": 1, "inconclusive": 1,
    }
    assert ars.hypothesis_tally(None) == {
        "validated": 0, "invalidated": 0, "inconclusive": 0,
    }


@pytest.mark.asyncio
async def test_collect_investigations_serializes_persisted_rows_verbatim():
    """The collector READS agent_investigations rows — basis comes from the
    winning hypothesis, the narrative is quoted, nothing is re-derived."""
    inv = SimpleNamespace(
        id=uuid.UUID(int=42),
        run_id=uuid.UUID(int=7),
        mode="shadow",
        status="completed",
        verdict={
            "primary_cause": "commit",
            "confidence": 65,
            "narrative": "Commit onset at abc123. Baseline was green.",
            "recommended_actions": ["review the diff"],
        },
        hypotheses=[
            {"id": "commit", "status": "validated", "confidence_basis": "llm_weighted"},
            {"id": "infra", "status": "invalidated", "confidence_basis": "heuristic_estimate"},
            {"id": "regression", "status": "inconclusive", "confidence_basis": "heuristic_estimate"},
        ],
        completed_at=NOW,
    )
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    rows_result = MagicMock()
    rows_result.all.return_value = [(inv, "b42")]
    review = SimpleNamespace(
        id=uuid.uuid4(), state="accepted", reviewed_at=NOW,
    )
    review_result = MagicMock()
    review_result.scalars.return_value.first.return_value = review
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, rows_result, review_result])

    data = await ars._collect_investigations(db, uuid.uuid4(), SINCE, NOW, cap=5)

    assert data["total"] == 1
    item = data["items"][0]
    assert item["run_build"] == "b42"
    assert item["run_id"] == str(uuid.UUID(int=7))
    assert item["primary_cause"] == "commit"
    assert item["confidence"] == 65
    assert item["confidence_basis"] == "llm_weighted"  # winning hypothesis's basis
    assert item["narrative_excerpt"] == "Commit onset at abc123. Baseline was green."
    assert item["narrative_review_state"] == "accepted"
    assert item["narrative_withheld"] is False
    assert item["hypothesis_tally"] == {
        "validated": 1, "invalidated": 1, "inconclusive": 1,
    }


@pytest.mark.asyncio
async def test_collector_withholds_pending_narrative_before_report_render(
    monkeypatch,
):
    from app.services import report_distribution_policy as distribution

    inv = SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        mode="shadow",
        verdict={
            "primary_cause": "infra",
            "confidence": 80,
            "narrative": "Sensitive unreviewed Investigator conclusion.",
        },
        hypotheses=[],
        completed_at=NOW,
    )
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    rows_result = MagicMock()
    rows_result.all.return_value = [(inv, "b-pending")]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[count_result, rows_result])
    decision = SimpleNamespace(
        allowed=False,
        envelope=SimpleNamespace(state="pending_review"),
    )
    gate = AsyncMock(
        return_value=(distribution.INVESTIGATION_REVIEW_PENDING_NOTICE, decision)
    )
    audit = AsyncMock()
    monkeypatch.setattr(distribution, "gate_investigation_excerpt", gate)
    monkeypatch.setattr(distribution, "record_distribution_detached", audit)

    data = await ars._collect_investigations(
        db,
        uuid.uuid4(),
        SINCE,
        NOW,
        cap=5,
        channel="digest_attachment",
    )

    item = data["items"][0]
    assert item["narrative_excerpt"] == distribution.INVESTIGATION_REVIEW_PENDING_NOTICE
    assert item["narrative_withheld"] is True
    assert "Sensitive unreviewed" not in str(item)
    assert gate.await_args.kwargs["investigation_id"] == inv.id
    assert gate.await_args.kwargs["channel"] == "digest_attachment"
    assert len(gate.await_args.kwargs["evidence_bundle_sha256"]) == 64
    audit.assert_awaited_once()


def test_report_replaces_a_withheld_narrative_with_review_guidance():
    from app.services.report_distribution_policy import (
        INVESTIGATION_REVIEW_PENDING_NOTICE,
    )

    item = _inv_item(
        narrative_excerpt=INVESTIGATION_REVIEW_PENDING_NOTICE,
        narrative_withheld=True,
        narrative_review_state="pending_review",
    )
    html = ars.render_analysis_report_html(_report_data([item]))

    assert INVESTIGATION_REVIEW_PENDING_NOTICE in html
    assert "withheld pending review" in html
    assert "connection-refused signatures" not in html


# ═══ 2. Digest delta line ════════════════════════════════════════════════════


def test_investigator_delta_line_none_when_zero_or_missing():
    assert dcs.investigator_delta_line(_delta()) is None
    assert dcs.investigator_delta_line({"new_failures": 3}) is None  # key absent


def test_investigator_delta_line_formats_tops_with_cause_display():
    delta = _delta(investigations={
        "completed": 3,
        "top": [
            {"run_build": "142", "primary_cause": "infra", "confidence": 78},
            {"run_build": "143", "primary_cause": "commit", "confidence": 55},
        ],
    })
    line = dcs.investigator_delta_line(delta)
    assert line == (
        "AI Investigator (shadow — informational): 3 investigations completed"
        " — #142 → infrastructure (78%), #143 → code change (55%)"
        " — evidence in the report"
    )


def test_digest_text_carries_investigator_line_only_when_present():
    with_inv = _digest(delta=_delta(
        new_failures=1,
        investigations={"completed": 1, "top": [
            {"run_build": "9", "primary_cause": "regression", "confidence": 80},
        ]},
    ), is_zero_change=False)
    text = dcs.render_digest_text(with_inv)
    assert "AI Investigator (shadow — informational): 1 investigation completed" in text
    assert "#9 → regression (80%)" in text

    without = _digest(delta=_delta(new_failures=1), is_zero_change=False)
    assert "AI Investigator" not in dcs.render_digest_text(without)


def test_digest_html_carries_escaped_investigator_line_only_when_present():
    with_inv = _digest(delta=_delta(
        new_failures=1,
        investigations={"completed": 1, "top": [
            {"run_build": HOSTILE, "primary_cause": "infra", "confidence": 70},
        ]},
    ), is_zero_change=False)
    html = dcs.render_digest_html(with_inv)
    assert "AI Investigator (shadow — informational)" in html
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html

    without = _digest(delta=_delta(new_failures=1), is_zero_change=False)
    assert "AI Investigator" not in dcs.render_digest_html(without)


def test_zero_change_predicate_ignores_investigations():
    """Completed investigations are informational — a window whose only
    activity is investigations still collapses to the one-liner."""
    delta = _delta(investigations={"completed": 2, "top": []})
    assert dcs.is_zero_change(delta) is True


# ═══ 3. Weekly flaky-debt review ═════════════════════════════════════════════


def test_next_step_priority_promote_over_stale_over_new():
    base_url = "http://dash.local"
    promote = fdr.suggest_next_step(
        {"ready_to_promote": True, "stale": True, "newly_flaky": True,
         "consecutive_passes": 6}, base_url)
    assert promote.startswith("Promote — ")
    assert "6 consecutive passing runs" in promote
    assert f"{base_url}/quarantine" in promote

    stale = fdr.suggest_next_step(
        {"stale": True, "newly_flaky": True, "stale_at": "2026-07-01T00:00:00+00:00"},
        base_url)
    assert stale.startswith("Review — ")
    assert "stale since 2026-07-01" in stale

    new = fdr.suggest_next_step({"newly_flaky": True}, base_url)
    assert new.startswith("Investigate — ")
    assert f"{base_url}/flaky-coach" in new

    default = fdr.suggest_next_step({}, base_url)
    assert default.startswith("Review reason — ")


def test_group_entries_by_team_buckets_and_multi_membership():
    entries = [
        _debt_entry(),  # payments, active only
        _debt_entry(test_name="test_stale", stale=True),  # payments: active + stale
        _debt_entry(test_name="test_promote", team="checkout",
                    active=True, ready_to_promote=True),
        _debt_entry(test_name="test_new", team=None, active=False, newly_flaky=True,
                    status="PROPOSED"),
    ]
    grouped = fdr.group_entries_by_team(entries)
    assert set(grouped) == {"payments", "checkout", fdr.UNASSIGNED_TEAM}
    assert len(grouped["payments"]["quarantined"]) == 2
    assert len(grouped["payments"]["stale"]) == 1
    assert len(grouped["checkout"]["ready_to_promote"]) == 1
    assert len(grouped[fdr.UNASSIGNED_TEAM]["newly_flaky"]) == 1
    assert grouped[fdr.UNASSIGNED_TEAM]["quarantined"] == []


def test_render_team_review_text_label_buckets_and_owner():
    buckets = {
        "quarantined": [_debt_entry()],
        "stale": [],
        "ready_to_promote": [_debt_entry(test_name="test_promote",
                                         ready_to_promote=True)],
        "newly_flaky": [],
    }
    text = fdr.render_team_review_text(
        "payments", buckets,
        window_start=SINCE.isoformat(), window_end=NOW.isoformat(),
        base_url="http://dash.local",
    )
    assert text is not None
    assert text.startswith("Weekly flaky-debt review — payments")
    assert fdr.REVIEW_LABEL in text
    assert "Quarantined (active) (1):" in text
    assert "Ready to promote (1):" in text
    assert "Stale over SLA" not in text        # empty buckets omitted
    assert "owner: alice@x.com" in text
    assert "next: " in text
    assert "http://dash.local/quarantine" in text


def test_render_team_review_text_none_when_all_buckets_empty():
    empty = {"quarantined": [], "stale": [], "ready_to_promote": [], "newly_flaky": []}
    assert fdr.render_team_review_text(
        "payments", empty,
        window_start=SINCE.isoformat(), window_end=NOW.isoformat(),
        base_url="http://x",
    ) is None


def test_render_team_review_text_caps_bucket_rows():
    entries = [_debt_entry(test_name=f"t{i}") for i in range(fdr.BUCKET_ROW_CAP + 3)]
    buckets = {"quarantined": entries, "stale": [], "ready_to_promote": [], "newly_flaky": []}
    text = fdr.render_team_review_text(
        "payments", buckets,
        window_start=SINCE.isoformat(), window_end=NOW.isoformat(),
        base_url="http://x",
    )
    assert f"t{fdr.BUCKET_ROW_CAP - 1}" in text
    assert f"t{fdr.BUCKET_ROW_CAP}" not in text
    assert "+3 more" in text


@pytest.mark.asyncio
async def test_build_reviews_sorts_by_debt_and_omits_empty_teams():
    entries = [
        _debt_entry(team="checkout"),
        _debt_entry(team="payments"),
        _debt_entry(team="payments", test_name="t2", stale=True),
    ]
    with patch.object(fdr, "collect_flaky_debt_entries",
                      new=AsyncMock(return_value=entries)):
        reviews = await fdr.build_flaky_debt_reviews(
            MagicMock(), uuid.uuid4(), since=SINCE, now=NOW,
        )
    assert [t["team"] for t in reviews["teams"]] == ["payments", "checkout"]
    payments = reviews["teams"][0]
    assert payments["counts"] == {
        "ready_to_promote": 0, "stale": 1, "newly_flaky": 0, "quarantined": 2,
    }
    assert fdr.REVIEW_LABEL in payments["text"]


@pytest.mark.asyncio
async def test_build_reviews_empty_project_yields_no_teams():
    with patch.object(fdr, "collect_flaky_debt_entries",
                      new=AsyncMock(return_value=[])):
        reviews = await fdr.build_flaky_debt_reviews(MagicMock(), uuid.uuid4(), now=NOW)
    assert reviews["teams"] == []


# ── Digest fold-in (teams without a channel) ────────────────────────────────


def _quiet_db() -> MagicMock:
    """A db mock that satisfies generate_digest's empty-project queries."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    result.scalar.return_value = 0
    result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_weekly_digest_folds_in_only_channel_less_teams():
    reviews = {
        "window_start": SINCE.isoformat(), "window_end": NOW.isoformat(),
        "teams": [
            {"team": "payments", "counts": {}, "text": "payments draft"},
            {"team": "checkout", "counts": {}, "text": "checkout draft"},
        ],
    }
    with (
        patch("app.services.flaky_debt_review.build_flaky_debt_reviews",
              new=AsyncMock(return_value=reviews)),
        patch("app.services.notification_routing.load_team_channels",
              new=AsyncMock(return_value={"payments": object()})),
    ):
        digest = await dcs.generate_digest(_quiet_db(), uuid.uuid4(), "weekly")
    review = digest["flaky_debt_review"]
    assert [t["team"] for t in review["teams"]] == ["checkout"]
    assert review["routed_team_count"] == 1


@pytest.mark.asyncio
async def test_daily_digest_never_carries_flaky_debt_review():
    with patch("app.services.flaky_debt_review.build_flaky_debt_reviews",
               new=AsyncMock()) as build:
        digest = await dcs.generate_digest(_quiet_db(), uuid.uuid4(), "daily")
    build.assert_not_awaited()
    assert "flaky_debt_review" not in digest


@pytest.mark.asyncio
async def test_weekly_digest_with_zero_debt_adds_no_section():
    reviews = {"window_start": SINCE.isoformat(), "window_end": NOW.isoformat(), "teams": []}
    with (
        patch("app.services.flaky_debt_review.build_flaky_debt_reviews",
              new=AsyncMock(return_value=reviews)),
        patch("app.services.notification_routing.load_team_channels",
              new=AsyncMock(return_value={})),
    ):
        digest = await dcs.generate_digest(_quiet_db(), uuid.uuid4(), "weekly")
    assert "flaky_debt_review" not in digest
    assert "flaky-debt" not in dcs.render_digest_text(digest).lower()
    assert "flaky-debt" not in dcs.render_digest_html(digest).lower()


@pytest.mark.asyncio
async def test_weekly_digest_review_fault_never_kills_the_digest():
    with patch("app.services.flaky_debt_review.build_flaky_debt_reviews",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        digest = await dcs.generate_digest(_quiet_db(), uuid.uuid4(), "weekly")
    assert "flaky_debt_review" not in digest
    assert digest["period"] == "weekly"


def test_digest_renderings_carry_folded_review_text_escaped():
    digest = _digest(flaky_debt_review={
        "teams": [{"team": "checkout", "counts": {},
                   "text": f"Weekly flaky-debt review — checkout\n{fdr.REVIEW_LABEL}\n  - {HOSTILE}"}],
        "routed_team_count": 0,
    })
    text = dcs.render_digest_text(digest)
    assert "Weekly flaky-debt review — checkout" in text
    assert fdr.REVIEW_LABEL in text

    html = dcs.render_digest_html(digest)
    assert "Weekly flaky-debt review" in html
    # The automation label survives (html.escape turns the apostrophe into
    # an entity, so assert on apostrophe-free fragments).
    assert "Automated draft" in html
    assert "verify before acting" in html
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


# ── Team-channel delivery loop ───────────────────────────────────────────────


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_deliver_sends_only_channel_mapped_teams_and_logs():
    project_id = uuid.uuid4()
    ids_result = MagicMock()
    ids_result.all.return_value = [(project_id,)]
    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Acme"
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[ids_result, name_result])

    channel = SimpleNamespace(team_name="payments", channel_type="slack", target="http://hook")
    reviews = {
        "window_start": SINCE.isoformat(), "window_end": NOW.isoformat(),
        "teams": [
            {"team": "payments", "counts": {}, "text": "payments draft"},
            {"team": "checkout", "counts": {}, "text": "checkout draft"},  # no channel
        ],
    }
    send = AsyncMock(return_value=("sent", None))
    record = AsyncMock()
    session_factory = MagicMock(side_effect=lambda: _SessionCtx(db))

    with (
        patch("app.db.postgres.AsyncSessionLocal", session_factory),
        patch("app.services.notification_routing.load_team_channels",
              new=AsyncMock(return_value={"payments": channel})),
        patch.object(fdr, "build_flaky_debt_reviews", new=AsyncMock(return_value=reviews)),
        patch("app.services.notification_routing.send_to_team_channel", send),
        patch("app.services.notification_routing.record_team_delivery_logs", record),
    ):
        counters = await fdr.deliver_flaky_debt_reviews(now=NOW)

    assert counters == {"projects": 1, "sent": 1, "failed": 0}
    send.assert_awaited_once()
    args = send.await_args.args
    assert args[0] is channel
    assert "payments" in args[1]           # title carries the team
    assert args[2] == "payments draft"
    assert args[3] == "flaky_debt_review"
    record.assert_awaited_once()
    log_entries = record.await_args.args[2]
    assert len(log_entries) == 1 and log_entries[0][4] == "sent"


@pytest.mark.asyncio
async def test_deliver_is_project_fail_open():
    """One broken project must not starve the rest (and never raises)."""
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    ids_result = MagicMock()
    ids_result.all.return_value = [(p1,), (p2,)]
    name_result = MagicMock()
    name_result.scalar_one_or_none.return_value = "Acme"
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[ids_result, name_result])

    calls: list[uuid.UUID] = []

    async def _channels(_db, project_id):
        calls.append(project_id)
        if project_id == p1:
            raise RuntimeError("boom")
        return {}

    with (
        patch("app.db.postgres.AsyncSessionLocal",
              MagicMock(side_effect=lambda: _SessionCtx(db))),
        patch("app.services.notification_routing.load_team_channels", _channels),
    ):
        counters = await fdr.deliver_flaky_debt_reviews(now=NOW)
    assert calls == [p1, p2]
    assert counters["sent"] == 0


# ═══ 4. No-new-LLM-calls guard ═══════════════════════════════════════════════


def test_narrative_surfaces_never_import_llm_machinery():
    """Source tripwire: the report/digest/review modules must never grow an
    LLM dependency — narratives are QUOTED from persisted verdicts."""
    import inspect

    for module in (ars, dcs, fdr):
        src = inspect.getsource(module)
        assert "llm_factory" not in src, module.__name__
        assert "get_llm" not in src, module.__name__
        assert "prompt_registry" not in src, module.__name__


def test_rendering_paths_survive_llm_explosion(monkeypatch):
    """Runtime tripwire (same pattern as the Investigator's offline test):
    with get_llm rigged to explode, every AI-7 surface still renders."""
    import app.services.llm_factory as llm_factory

    def _explode(*_a, **_k):
        raise AssertionError("narrative surfaces must never construct an LLM")

    monkeypatch.setattr(llm_factory, "get_llm", _explode)

    html = ars.render_analysis_report_html(_report_data([_inv_item()]))
    assert "Agent investigations" in html

    digest = _digest(
        delta=_delta(investigations={"completed": 1, "top": [
            {"run_build": "1", "primary_cause": "infra", "confidence": 70},
        ]}),
        is_zero_change=False,
        flaky_debt_review={"teams": [
            {"team": "t", "counts": {}, "text": f"draft\n{fdr.REVIEW_LABEL}"},
        ], "routed_team_count": 0},
    )
    assert "AI Investigator" in dcs.render_digest_text(digest)
    assert "AI Investigator" in dcs.render_digest_html(digest)

    text = fdr.render_team_review_text(
        "payments",
        {"quarantined": [_debt_entry()], "stale": [], "ready_to_promote": [],
         "newly_flaky": []},
        window_start=SINCE.isoformat(), window_end=NOW.isoformat(),
        base_url="http://x",
    )
    assert fdr.REVIEW_LABEL in text
