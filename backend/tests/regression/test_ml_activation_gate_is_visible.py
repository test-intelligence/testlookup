"""Regression guard: the learning loop says why it is not learning (F-10).

The finding
-----------
``auto`` resolves ML → LLM → rules, and ML only wins once a trained model
exists — which needs ``ML_MIN_TRAINING_SAMPLES`` labelled corrections. Below
that the LLM runs and gains nothing from feedback except **exact-fingerprint**
replay: a human correcting ``test_checkout_timeout`` teaches the system nothing
about ``test_payments_timeout``, however identical the cause. The one mechanism
that would improve the LLM itself — fine-tuning — is off by default,
OpenAI-only, and refused under ``AI_OFFLINE_MODE``.

What measuring changed
----------------------
On a live deployment: **4,849 analyses and zero feedback rows.** The loop had
never been started at all, so the problem was not "stuck below 200" — nothing
was feeding it. The submission path is fully built and wired; it had simply
never been used.

That is why this reports the gate rather than building semantic correction
reuse. Retrieval over corrections would be a feature for data that does not
exist; the gap that *does* exist is that none of the above was visible.
``get_training_status`` reported the FINE-TUNE thresholds while omitting the ML
activation gate — the one that decides whether feedback changes anything.

What is guarded
---------------
* the activation gate is reported, not just the fine-tune thresholds;
* a cold start says how far off it is, in labels;
* the reason distinguishes "not enough labels" from "enough labels, no model";
* readiness never raises — a status endpoint that 500s tells you nothing;
* the fine-tune fields it already reported are unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from app.services import feedback_service  # noqa: E402


class _Row:
    def __init__(self, total: int, unexported: int, labelled: int):
        self.total = total
        self.unexported = unexported
        self.labelled = labelled


class _Result:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _DB:
    """One aggregate, three counts.

    The labelled-correction count is folded into the SAME query as the totals:
    test_feedback_stats_single_aggregate pins execute() to one call, and it is
    right to -- a status endpoint should not fan out queries to answer one
    question. This fake counts calls so that stays true.
    """

    def __init__(self, total=0, unexported=0, labelled=0):
        self._row = _Row(total, unexported, labelled)
        self.calls = 0

    async def execute(self, _stmt):
        self.calls += 1
        return _Result(self._row)


_SETTINGS = SimpleNamespace(
    FINETUNE_ENABLED=False,
    FINETUNE_CLASSIFIER_MIN_EXAMPLES=500,
    FINETUNE_REASONING_MIN_EXAMPLES=2000,
    FINETUNE_EMBED_MIN_PAIRS=1000,
    FINETUNE_INCREMENTAL_TRIGGER=200,
    ML_MIN_TRAINING_SAMPLES=200,
)


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    from app.services import model_registry

    monkeypatch.setattr(
        model_registry.ModelRegistry, "get_all_status", AsyncMock(return_value={})
    )


def _no_model(monkeypatch):
    from app.services.ml import classifier

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(lambda: False))


def _has_model(monkeypatch):
    from app.services.ml import classifier

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(lambda: True))


# ── The gate is reported at all ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_activation_gate_is_reported(monkeypatch):
    """Reporting only the fine-tune thresholds left the gate that actually
    decides whether feedback matters invisible."""
    _no_model(monkeypatch)

    status = await feedback_service.get_training_status(_DB(labelled=0), _SETTINGS)

    assert "ml_activation" in status
    assert status["ml_activation"]["min_required"] == 200


@pytest.mark.asyncio
async def test_a_cold_start_says_how_far_off_it_is(monkeypatch):
    """The measured state of a real deployment: zero labels, nothing said so."""
    _no_model(monkeypatch)

    gate = (await feedback_service.get_training_status(_DB(labelled=0), _SETTINGS))["ml_activation"]

    assert gate["labelled_corrections"] == 0
    assert gate["model_available"] is False
    assert gate["auto_resolves_to"] != "ml"
    assert "0 of 200" in gate["blocked_reason"]


@pytest.mark.asyncio
async def test_it_names_exact_fingerprint_replay_as_the_only_effect(monkeypatch):
    """Below the gate, a correction only helps the identical test. Saying so is
    the difference between 'feedback does nothing' and 'feedback does little'."""
    _no_model(monkeypatch)

    gate = (await feedback_service.get_training_status(_DB(labelled=5), _SETTINGS))["ml_activation"]

    assert "fingerprint" in gate["blocked_reason"]


# ── The two blocked states are distinguishable ───────────────────────────────


@pytest.mark.asyncio
async def test_enough_labels_but_no_model_is_a_different_reason(monkeypatch):
    """Otherwise 'not learning' covers two problems with different fixes:
    collect more labels, versus train the model you can already train."""
    _no_model(monkeypatch)

    gate = (await feedback_service.get_training_status(_DB(labelled=250), _SETTINGS))["ml_activation"]

    assert "no trained model" in gate["blocked_reason"]
    assert "of 200 labelled" not in gate["blocked_reason"]


@pytest.mark.asyncio
async def test_an_active_model_is_not_reported_as_blocked(monkeypatch):
    _has_model(monkeypatch)

    gate = (await feedback_service.get_training_status(_DB(labelled=250), _SETTINGS))["ml_activation"]

    assert gate["model_available"] is True
    assert gate["auto_resolves_to"] == "ml"
    assert gate["blocked_reason"] is None


# ── Robustness, and no regression to what it already reported ────────────────


@pytest.mark.asyncio
async def test_readiness_never_raises_when_the_classifier_cannot_be_probed(monkeypatch):
    """A status endpoint that 500s tells you less than one that says 'unknown'."""
    from app.services.ml import classifier

    def _boom():
        raise RuntimeError("model dir unreadable")

    monkeypatch.setattr(classifier.MLClassifier, "is_available", staticmethod(_boom))

    gate = (await feedback_service.get_training_status(_DB(labelled=1), _SETTINGS))["ml_activation"]

    assert gate["model_available"] is False


@pytest.mark.asyncio
async def test_the_existing_finetune_fields_are_unchanged(monkeypatch):
    _no_model(monkeypatch)

    status = await feedback_service.get_training_status(_DB(total=7, unexported=3), _SETTINGS)

    assert status["finetune_enabled"] is False
    assert status["feedback"] == {"total": 7, "unexported": 3}
    assert status["thresholds"]["classifier"] == 500


@pytest.mark.asyncio
async def test_the_gate_costs_no_extra_query(monkeypatch):
    """Reporting readiness must not fan out round-trips; an existing guard
    pins execute() to a single call and this keeps it honest here too."""
    _no_model(monkeypatch)
    db = _DB(total=1, unexported=1, labelled=3)

    await feedback_service.get_training_status(db, _SETTINGS)

    assert db.calls == 1
