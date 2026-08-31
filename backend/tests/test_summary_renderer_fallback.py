"""
Coverage slice — the deterministic summary a human reads when the LLM is down.

``summary_renderer`` produces the developer- and manager-mode narratives that
``SummaryAgent`` serves. When an LLM is unavailable (offline mode, an outage, a
malformed response), the render functions fall back to a *deterministic* summary
assembled from the already-computed context. That fallback is what a human
actually reads during exactly the incidents that matter most, so its wording and
shape are load-bearing: a wrong pass-rate, a dropped recommendation, or an empty
action list is a defect a reader would act on.

Before this file the module sat at ~24% — the module-level prompt loads ran, but
neither render entry point nor any of the three fallback builders had executed. A
mistake in the f-strings (a swapped field, a lost ``:.1f``, a broken ``_``→space
substitution) would surface only to whoever was reading the summary mid-incident,
which nothing alerts on.

Every expected value here was read off the real implementation in
``app/services/summary_renderer.py`` before being asserted, not assumed. The LLM
is either short-circuited (a truthy ``fallback_reason``) or mocked — no network,
no model, no API key.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.summary_renderer import (
    _fallback_developer,
    _fallback_manager,
    _format_similar,
    render_developer_summary,
    render_manager_summary,
)


def _assembled() -> dict:
    """A realistic assembled-context dict, shaped the way SummaryAgent builds it."""
    return {
        "facts": {
            "build": "build-4711",
            "branch": "main",
            "total_tests": 200,
            "failed_tests": 12,
            "pass_rate": 94.0,
            "top_category": "PRODUCT_BUG",
            "cluster_count": 3,
        },
        "top_analyses": [
            {
                "root_cause_summary": "Null pointer in PaymentGateway.charge",
                "recommended_actions": ["Add null guard", "Cover with a unit test"],
            },
            {
                "root_cause_summary": "Timeout talking to the ledger service",
                "recommended_actions": ["Raise the client timeout", "Add a retry"],
            },
        ],
        "evidence_snippets": [
            {
                "source": "stacktrace",
                "excerpt": "java.lang.NullPointerException at PaymentGateway.charge(PaymentGateway.java:87)",
                "test_id": "t-1",
            },
            {
                "source": "logs",
                "excerpt": "ERROR ledger client timed out after 5000ms",
                "test_id": "t-2",
            },
        ],
        "similar_failures": [
            {"test_name": "testCharge"},
            {"test_name": "testRefund"},
        ],
        "release_inputs": {
            "recommendation": "CONDITIONAL_GO",
            "blocking_issues": ["Payment path regression", "Ledger latency"],
            "conditions_for_go": ["Fix null guard", "Confirm ledger SLA"],
        },
    }


# ── _fallback_developer ────────────────────────────────────────────────────


def test_fallback_developer_shape_and_values():
    result = _fallback_developer(_assembled(), reason="llm offline")

    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm offline"
    assert result["citations"] == []
    # headline pulls build / failed count / pass rate at one decimal place.
    assert result["headline"] == "Build build-4711 has 12 failures (94.0% pass rate)"
    # root cause is the first analysis' summary.
    assert result["root_cause_analysis"] == "Null pointer in PaymentGateway.charge"
    # actions: two per analysis (top 3 analyses), then capped at three.
    assert result["fix_recommendations"] == [
        "Add null guard",
        "Cover with a unit test",
        "Raise the client timeout",
    ]
    # validation steps are a fixed single-item list.
    assert result["validation_steps"] == ["Re-run failing tests after applying fixes"]
    # evidence highlights are the first two excerpts, each truncated to 100 chars.
    assert result["evidence_highlights"] == [
        "java.lang.NullPointerException at PaymentGateway.charge(PaymentGateway.java:87)",
        "ERROR ledger client timed out after 5000ms",
    ]
    assert (
        result["similar_historical_context"]
        == "Similar failures found: testCharge, testRefund"
    )


def test_fallback_developer_excerpt_truncated_to_100_chars():
    a = _assembled()
    long = "x" * 250
    a["evidence_snippets"] = [{"source": "logs", "excerpt": long}]
    result = _fallback_developer(a, reason=None)

    assert result["evidence_highlights"] == ["x" * 100]
    assert result["fallback_reason"] is None


def test_fallback_developer_caps_actions_at_three_across_analyses():
    a = _assembled()
    # Three analyses, two actions each → six candidates, capped to three.
    a["top_analyses"] = [
        {"root_cause_summary": "rc1", "recommended_actions": ["a1", "a2", "a3"]},
        {"root_cause_summary": "rc2", "recommended_actions": ["b1", "b2"]},
        {"root_cause_summary": "rc3", "recommended_actions": ["c1", "c2"]},
    ]
    result = _fallback_developer(a, reason="x")
    # Only the first two actions of each analysis are taken, then the list is capped.
    assert result["fix_recommendations"] == ["a1", "a2", "b1"]


def test_fallback_developer_defaults_when_no_analyses():
    a = _assembled()
    a["top_analyses"] = []
    a["evidence_snippets"] = []
    result = _fallback_developer(a, reason=None)

    assert result["root_cause_analysis"] == "No high-confidence root causes found"
    assert result["fix_recommendations"] == [
        "Review failing tests and investigate root cause"
    ]
    assert result["evidence_highlights"] == []


def test_fallback_developer_tolerates_empty_assembled():
    # Every lookup is a .get with a default; an empty context must not raise.
    result = _fallback_developer({}, reason=None)
    assert result["headline"] == "Build None has 0 failures (0.0% pass rate)"
    assert result["fix_recommendations"] == [
        "Review failing tests and investigate root cause"
    ]


# ── _fallback_manager ──────────────────────────────────────────────────────


def test_fallback_manager_shape_and_values():
    result = _fallback_manager(_assembled(), reason="llm offline")

    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "llm offline"
    assert result["citations"] == []
    # underscores in category and recommendation become spaces; category lowercased.
    assert result["executive_summary"] == (
        "Build build-4711 completed with 12 failures and 94.0% pass rate. "
        "The dominant failure type is product bug. "
        "Release recommendation: CONDITIONAL GO."
    )
    assert result["release_recommendation"] == (
        "CONDITIONAL_GO — based on 94.0% pass rate and failure analysis"
    )
    assert result["scope_of_impact"] == "3 failure clusters across 12 tests"
    # key risks / decisions are the first two of each release-input list.
    assert result["key_risks"] == ["Payment path regression", "Ledger latency"]
    assert result["recommended_decisions"] == ["Fix null guard", "Confirm ledger SLA"]
    assert result["timeline_guidance"] == "Review before next scheduled release"


def test_fallback_manager_defaults_when_release_inputs_empty():
    a = _assembled()
    a["release_inputs"] = {}
    a["facts"]["top_category"] = "UNKNOWN"
    result = _fallback_manager(a, reason=None)

    # recommendation defaults to CONDITIONAL_GO when absent.
    assert "Release recommendation: CONDITIONAL GO." in result["executive_summary"]
    assert result["release_recommendation"].startswith("CONDITIONAL_GO — based on")
    assert result["key_risks"] == ["Review failing tests before release"]
    assert result["recommended_decisions"] == ["Assess release readiness manually"]


def test_fallback_manager_tolerates_empty_assembled():
    result = _fallback_manager({}, reason=None)
    assert result["scope_of_impact"] == "0 failure clusters across 0 tests"
    assert result["fallback_used"] is True


# ── _format_similar ────────────────────────────────────────────────────────


def test_format_similar_empty():
    assert _format_similar([]) == "No similar historical failures found"


def test_format_similar_with_names_takes_first_three():
    similar = [
        {"test_name": "a"},
        {"test_name": "b"},
        {"test_name": "c"},
        {"test_name": "d"},
    ]
    assert _format_similar(similar) == "Similar failures found: a, b, c"


def test_format_similar_present_but_no_names():
    # Non-empty list whose entries carry no usable name → generic phrasing,
    # never an empty "Similar failures found: ".
    assert (
        _format_similar([{"error_message": "boom"}, {"test_name": ""}])
        == "Similar failures found in recent runs"
    )


# ── async entry points: fallback short-circuit ─────────────────────────────


async def test_render_developer_summary_falls_back_on_reason():
    # A truthy fallback_reason skips the LLM branch entirely.
    result = await render_developer_summary(_assembled(), fallback_reason="offline")
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "offline"
    assert result["headline"] == "Build build-4711 has 12 failures (94.0% pass rate)"


async def test_render_manager_summary_falls_back_on_reason():
    result = await render_manager_summary(_assembled(), fallback_reason="offline")
    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "offline"
    assert result["scope_of_impact"] == "3 failure clusters across 12 tests"


async def test_render_developer_summary_falls_back_when_llm_raises():
    # No fallback_reason → the LLM branch runs; when get_llm raises, the failure
    # is caught, its message becomes the fallback_reason, and a fallback is served.
    with patch(
        "app.services.llm_factory.get_llm",
        AsyncMock(side_effect=RuntimeError("no model")),
    ):
        result = await render_developer_summary(_assembled())
    assert result["fallback_used"] is True
    assert "no model" in result["fallback_reason"]


async def test_render_manager_summary_falls_back_when_llm_raises():
    with patch(
        "app.services.llm_factory.get_llm",
        AsyncMock(side_effect=RuntimeError("no model")),
    ):
        result = await render_manager_summary(_assembled())
    assert result["fallback_used"] is True
    assert "no model" in result["fallback_reason"]


# ── async entry points: LLM success path ───────────────────────────────────


def _mock_llm_returning(payload: dict) -> MagicMock:
    """A get_llm stub whose model echoes ``payload`` as fenced JSON."""
    resp = MagicMock()
    resp.content = "```json\n" + json.dumps(payload) + "\n```"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


async def test_render_developer_summary_parses_llm_json_and_cites():
    # The excerpt's opening phrase appears verbatim in the model output, so the
    # deterministic citation extractor should attach it.
    excerpt = "java.lang.NullPointerException at PaymentGateway.charge"
    assembled = {
        "facts": {"build": "b1", "failed_tests": 1, "pass_rate": 99.0},
        "evidence_snippets": [
            {"source": "stacktrace", "excerpt": excerpt, "test_id": "t-1"}
        ],
    }
    payload = {
        "headline": "One failure",
        "root_cause_analysis": excerpt,
    }
    llm = _mock_llm_returning(payload)
    with patch("app.services.llm_factory.get_llm", AsyncMock(return_value=llm)):
        result = await render_developer_summary(assembled)

    assert result["fallback_used"] is False
    assert result["headline"] == "One failure"
    assert result["citations"] == [
        {"source": "stacktrace", "excerpt": excerpt, "test_id": "t-1"}
    ]


async def test_render_manager_summary_parses_llm_json():
    payload = {"executive_summary": "All green", "release_recommendation": "GO"}
    llm = _mock_llm_returning(payload)
    assembled = {"facts": {"build": "b1", "pass_rate": 100.0}}
    with patch("app.services.llm_factory.get_llm", AsyncMock(return_value=llm)):
        result = await render_manager_summary(assembled)

    assert result["fallback_used"] is False
    assert result["executive_summary"] == "All green"
    assert result["citations"] == []
