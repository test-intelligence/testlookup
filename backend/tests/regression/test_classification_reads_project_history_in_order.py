"""A test's history is read from its own project, in run order (D-02, D-07, S-A-6).

Seen on a running install, rules mode, project Storefront Web (52 runs in 30
days). Four coupon tests passed for weeks, then failed on every run after one
commit, with the same assertion error each time. Each failure read:

    FLAKY · estimated 85% · "Test has a 15% historical failure rate (16/106
    runs). Exhibits flaky behavior — passes and fails non-deterministically."
    Recommended: "Quarantine test to prevent blocking releases"

Two faults:

* D-07. 106 is about twice the project's run count. The history query filtered
  on the test fingerprint only, and a fingerprint is ``hash(test_name +
  class_name)``, shared by every project holding the same test. A second
  project's results were counted into this one's.
* D-02. The flaky rule read only two counts. Order was never consulted, so a
  passing streak followed by a failing streak (a regression) looked the same
  as results that flip at random. ``confidence_validation`` then re-derived
  ``is_flaky`` from the same counts for every verdict, which dropped such
  failures from the release score's product bugs (S-A-6, D-97).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.agents.analysis_agent import AnalysisAgent
from app.services import test_history_service
from app.services.confidence_validation import validate_confidence
from app.services.flaky_signals import history_is_flaky, is_regression_history
from app.services.rules_engine import RulesEngine
from app.services.test_history_service import load_test_history, summarise_history

_COUPON_ERROR = "AssertionError: expected total 90.00 but was 100.00"


def _records(sequence: str, error: str = _COUPON_ERROR) -> list[dict]:
    """``P`` passed, ``F`` failed; oldest first."""
    return [
        {"status": "PASSED"} if c == "P" else {"status": "FAILED", "error_message": error}
        for c in sequence
    ]


def _regression() -> dict:
    # 43 passes, then 9 failures in a row with one error: the coupon tests.
    return summarise_history(_records("P" * 43 + "F" * 9))


def _flipping() -> dict:
    # Fails about one run in three, scattered, with varying errors.
    seq = "PPFPPFPFPPPFPPFPPPFPFPPF"
    records = []
    for i, c in enumerate(seq):
        records.append(
            {"status": "PASSED"}
            if c == "P"
            else {"status": "FAILED", "error_message": f"TimeoutError after {1000 + i * 37} ms"}
        )
    return summarise_history(records)


# ── The history summary ──────────────────────────────────────────────────


def test_the_summary_measures_the_current_streak():
    history = _regression()

    assert history["pass_count"] == 43
    assert history["fail_count"] == 9
    assert history["consecutive_failures"] == 9
    assert history["consecutive_passes"] == 43
    assert history["intermittency_label"] == "persistent_regression"


def test_skipped_results_do_not_break_a_streak():
    records = _records("PPPPP") + [{"status": "SKIPPED"}] + _records("FF")
    history = summarise_history(records)

    assert history["consecutive_failures"] == 2
    assert history["consecutive_passes"] == 5


# ── The verdict helpers ──────────────────────────────────────────────────


def test_a_passing_streak_then_failing_every_run_is_a_regression():
    assert is_regression_history(_regression())
    assert not history_is_flaky(_regression())


def test_results_that_flip_are_flaky_not_a_regression():
    history = _flipping()

    assert not is_regression_history(history)
    assert history_is_flaky(history)


def test_counts_without_order_keep_the_rate_rule():
    """Callers that still pass only counts are not changed."""
    assert history_is_flaky({"pass_count": 43, "fail_count": 9})
    assert not history_is_flaky({"pass_count": 0, "fail_count": 9})


# ── The rules engine ─────────────────────────────────────────────────────


def test_the_rules_engine_calls_the_coupon_regression_a_product_bug():
    result = RulesEngine.classify_test(error_message=_COUPON_ERROR, history=_regression())

    assert result["failure_category"] == "PRODUCT_BUG"
    assert result["is_flaky"] is False
    assert result["confidence_rule_id"] == "heuristic.regression_after_streak"
    assert "9 runs in a row" in result["root_cause_summary"]
    assert not any("Quarantine" in a for a in result["recommended_actions"])


def test_the_rules_engine_still_calls_flipping_results_flaky():
    result = RulesEngine.classify_test(
        error_message="TimeoutError after 1200 ms", history=_flipping()
    )

    assert result["failure_category"] == "FLAKY"
    assert result["is_flaky"] is True


def test_a_single_new_failure_after_a_streak_is_a_regression():
    history = summarise_history(_records("P" * 20 + "F"))
    result = RulesEngine.classify_test(error_message=_COUPON_ERROR, history=history)

    assert result["failure_category"] == "PRODUCT_BUG"
    assert result["confidence_rule_id"] == "heuristic.regression_after_streak"


# ── The confidence check no longer re-derives flaky from counts ──────────


def test_confidence_validation_keeps_a_regression_out_of_flaky():
    analysis = {
        "failure_category": "PRODUCT_BUG",
        "confidence_score": 70,
        "root_cause_summary": "Test failed the last 9 runs in a row with the same error.",
        "is_flaky": False,
        "flakiness_data": _regression(),
        "_routing": {"mode_used": "rules"},
    }

    assert validate_confidence(analysis)["is_flaky"] is False


def test_a_human_correction_is_not_capped_or_sent_back_to_review():
    analysis = {
        "failure_category": "PRODUCT_BUG",
        "confidence_score": 95,
        "root_cause_summary": "Classification set by human review.",
        "is_flaky": False,
        "human_corrected": True,
        "flakiness_data": {"pass_count": 43, "fail_count": 9},
    }

    validated = validate_confidence(analysis)

    assert validated["confidence_score"] == 95
    assert validated["requires_human_review"] is False
    assert validated["is_flaky"] is False


# ── The read is scoped to the run's project ──────────────────────────────


class _CapturingDB:
    def __init__(self, rows=()):
        self.statements = []
        self._rows = list(rows)

    async def execute(self, statement, *_args, **_kwargs):
        self.statements.append(statement)
        rows = self._rows
        return SimpleNamespace(all=lambda: rows)


@pytest.mark.asyncio
async def test_the_history_query_filters_on_the_project():
    project_id = uuid.uuid4()
    db = _CapturingDB()

    await load_test_history(db, project_id, ["fp-coupon"])

    assert len(db.statements) == 1
    sql = str(
        db.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "test_runs.project_id = '" + str(project_id) + "'" in sql
    assert "JOIN test_runs ON test_runs.id = test_cases.test_run_id" in sql


@pytest.mark.asyncio
async def test_no_project_means_no_history_read():
    db = _CapturingDB()

    assert await load_test_history(db, None, ["fp-coupon"]) == {}
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_pipeline_passes_its_project_to_the_history_read(monkeypatch):
    project_id = uuid.uuid4()
    seen = {}

    async def _loader(db, pid, fingerprints, **_kwargs):
        seen["project_id"] = pid
        seen["fingerprints"] = sorted(fingerprints)
        return {"fp-coupon": _regression()}

    monkeypatch.setattr(test_history_service, "load_test_history", _loader)
    meta = {"tc-1": {"test_name": "charges the discounted total"}}

    await AnalysisAgent()._enrich_historical_counts(
        object(), meta, {"tc-1": "fp-coupon"}, project_id
    )

    assert seen == {"project_id": project_id, "fingerprints": ["fp-coupon"]}
    assert meta["tc-1"]["flakiness_data"]["consecutive_failures"] == 9


@pytest.mark.asyncio
async def test_the_pipeline_reads_no_history_without_a_project(monkeypatch):
    async def _loader(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("history read without a project")

    monkeypatch.setattr(test_history_service, "load_test_history", _loader)
    meta = {"tc-1": {}}

    await AnalysisAgent()._enrich_historical_counts(object(), meta, {"tc-1": "fp"}, None)

    assert "flakiness_data" not in meta["tc-1"]
