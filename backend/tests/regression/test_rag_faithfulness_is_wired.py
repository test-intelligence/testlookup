"""Regression: the RAG faithfulness gate must actually be reachable.

Found during the LLM-integration sweep (AI-001, 2026-08-16):
``rag_faithfulness_service`` implemented a complete feature — ``evaluate``,
``gate_accept``, ``persist_evaluation``, ``list_needs_review``, two evaluator
backends, a feature flag, a score parser, columns and a migration (0069) — and
**nothing in ``app/`` called any of it**. The only reference outside the module
was a comment.

Its own tests passed, because they called it directly. A green suite proved the
code worked; it did not prove anything ran it. That is the session's dominant
defect class — something that exists but can never run — and a unit test can
never catch it by construction.

So the primary guard here is a REACHABILITY check: every public entry point
must have a caller in ``app/`` that is not the module itself. The behavioural
tests below then pin what the wiring does.

Note the safety property this feature is built on: the flag defaults OFF (a
missing flag row resolves False), so wiring it changes nothing for existing
deployments until someone opts in. The tests assert that too — a gate that
starts blocking work the moment it is deployed would be a worse defect than the
one being fixed.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
MODULE = "rag_faithfulness_service"

# The entry points the module exposes for other code to call.
PUBLIC_ENTRY_POINTS = ("evaluate", "gate_accept", "check_accept",
                       "apply_evaluation", "persist_evaluation",
                       "list_needs_review")


def _callers_of(name: str) -> list[str]:
    """Files under app/ (excluding the module itself) that call ``name``."""
    hits = []
    for path in APP.rglob("*.py"):
        if path.name == f"{MODULE}.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = (
                fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name)
                else None
            )
            if called == name:
                hits.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    return hits


def test_the_scanner_can_see_app_code():
    """Everything below is a search over app/. An empty tree passes vacuously."""
    assert (APP / "services" / f"{MODULE}.py").exists()
    assert len(list(APP.rglob("*.py"))) > 50


@pytest.mark.parametrize("entry", ["evaluate", "check_accept", "list_needs_review"])
def test_each_entry_point_has_a_production_caller(entry):
    """The regression itself. Unit tests calling a function do not make it run.

    ``gate_accept``/``persist_evaluation``/``apply_evaluation`` are deliberately
    excluded: the first two are the background/own-session forms kept for
    non-request callers, and ``apply_evaluation`` is exercised through
    ``evaluate``'s call site. The three checked here are the ones that make the
    feature reachable from a request.
    """
    callers = _callers_of(entry)
    assert callers, (
        f"rag_faithfulness_service.{entry}() has no caller anywhere in app/. "
        f"The feature is unreachable — its unit tests will still pass, because "
        f"they call it directly."
    )


def test_generation_evaluates_and_accept_gates():
    """Pin WHICH surfaces do the calling, not merely that someone does."""
    assert any("rag_generation_service" in c for c in _callers_of("evaluate")), (
        "nothing in rag_generation_service evaluates generated cases, so no case "
        "ever gets a faithfulness score and the gate has nothing to act on"
    )
    assert any("rag_review_service" in c for c in _callers_of("check_accept")), (
        "the accept path no longer consults the faithfulness gate"
    )


def test_the_needs_review_queue_is_exposed():
    src = (APP / "routers" / "rag_generation.py").read_text(encoding="utf-8")
    assert "needs-review" in src, (
        "no endpoint exposes the needs-review queue, so cases the gate holds "
        "back are invisible to the people meant to review them"
    )


def test_the_literal_route_is_registered_before_the_uuid_one():
    """`/cases/needs-review` must not be parsed as a `{case_id}`.

    This repo has hit that exact trap before — the comment in bootstrap.py
    about `/cases/stale` records it. Ordering is the fix, so pin the ordering.
    """
    src = (APP / "bootstrap.py").read_text(encoding="utf-8")
    rag = src.index("rag_generation.router")
    tm = src.index("test_management.router")
    assert rag < tm, (
        "rag_generation.router must be registered before test_management.router "
        "or the literal /cases/needs-review is swallowed by /cases/{case_id} "
        "and 422s on a non-UUID path segment"
    )


# ── Behaviour ───────────────────────────────────────────────────────────────


def _case(**kw):
    base = dict(
        id=uuid.uuid4(), faithfulness_score=None, faithfulness_evaluator=None,
        faithfulness_evaluated_at=None, needs_review_reason=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_gate_is_a_no_op_when_the_flag_is_off():
    """The safety property. Wiring a gate that blocks work on day one would be
    a worse defect than the unreachable one it replaces."""
    from app.services import rag_faithfulness_service as svc

    case = _case(faithfulness_score=0.01)  # would be blocked if the gate ran
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=False)):
        decision = await svc.check_accept(case)

    assert decision["allow"] is True
    assert case.needs_review_reason is None, "a disabled gate must not annotate rows"


@pytest.mark.asyncio
async def test_a_low_score_blocks_accept_and_records_why():
    from app.services import rag_faithfulness_service as svc

    case = _case(faithfulness_score=0.10, faithfulness_evaluator="ollama")
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)):
        decision = await svc.check_accept(case, threshold=0.70)

    assert decision["allow"] is False
    assert decision["score"] == 0.10
    assert decision["threshold"] == 0.70
    assert "0.10" in case.needs_review_reason and "0.70" in case.needs_review_reason
    assert "ollama" in case.needs_review_reason, (
        "the reason must name the evaluator — 'the AI said no' is not actionable"
    )


@pytest.mark.asyncio
async def test_a_passing_score_allows_accept_without_annotating():
    from app.services import rag_faithfulness_service as svc

    case = _case(faithfulness_score=0.95)
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)):
        decision = await svc.check_accept(case, threshold=0.70)

    assert decision["allow"] is True
    assert case.needs_review_reason is None


@pytest.mark.asyncio
async def test_an_unevaluated_case_is_not_blocked():
    """Turning the flag on must not retroactively block every case generated
    before it. Blocking on evidence that was never collected is the fail-closed
    version of the same dishonesty."""
    from app.services import rag_faithfulness_service as svc

    case = _case(faithfulness_score=None)
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)):
        decision = await svc.check_accept(case)

    assert decision["allow"] is True
    assert "predates" in decision["reason"]


def test_apply_evaluation_flags_a_low_score_for_review():
    from app.services.rag_faithfulness_service import apply_evaluation

    case = _case()
    apply_evaluation(case, {"score": 0.2, "evaluator": "ollama", "reason": "unsupported"})

    assert case.faithfulness_score == 0.2
    assert case.faithfulness_evaluator == "ollama"
    assert case.faithfulness_evaluated_at is not None
    assert "0.20" in case.needs_review_reason


def test_apply_evaluation_leaves_a_good_case_unflagged():
    from app.services.rag_faithfulness_service import apply_evaluation

    case = _case()
    apply_evaluation(case, {"score": 0.9, "evaluator": "ollama", "reason": "grounded"})

    assert case.faithfulness_score == 0.9
    assert case.needs_review_reason is None


def test_generation_stages_on_the_caller_session_not_its_own():
    """`persist_evaluation` opens its own session and commits. Calling it from
    inside generation would read a row that has only been flushed — a different
    session cannot see it, so it would silently score nothing while looking
    wired up. The in-request path must use `apply_evaluation`.
    """
    # Assert on the CALLS, not on substrings: the first version of this test
    # matched its own explanatory comment and failed for the wrong reason.
    # A phrase prose can satisfy is not a guard.
    gen = "rag_generation_service"
    assert any(gen in c for c in _callers_of("apply_evaluation")), (
        "generation no longer stages the evaluation on its own session"
    )
    assert not any(gen in c for c in _callers_of("persist_evaluation")), (
        "generation calls persist_evaluation, which opens a second session and "
        "cannot see the flushed-but-uncommitted case — it would score nothing "
        "and leave no trace of having failed"
    )


# ── The evaluator label must name what actually judged (AI-009) ─────────────
#
# Rows recorded `faithfulness_evaluator = "ollama"` and the refusal said
# "(evaluator: ollama)" — but that branch calls get_llm(), which on this
# deployment resolves to OpenRouter/mistral-nemo. A case judged by a hosted
# model was recorded as judged by Ollama.
#
# Same class as the badge that said "Model Missing" for a missing API key: a
# label naming the wrong thing. It matters more here because it is provenance
# on a gating decision.


@pytest.mark.asyncio
async def test_the_label_names_the_provider_that_judged_not_the_strategy():
    from app.services import rag_faithfulness_service as svc

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_resolve_backend", AsyncMock(return_value="ollama")), \
         patch.object(svc, "_active_llm_provider", AsyncMock(return_value="openrouter")), \
         patch.object(svc, "_evaluate_via_ollama",
                      AsyncMock(return_value=(0.9, "grounded"))):
        result = await svc.evaluate("a case", ["a citation"])

    assert result["evaluator"] == "openrouter", (
        f"evaluator recorded as {result['evaluator']!r}; the strategy is named "
        f"'ollama' but the provider that actually judged is OpenRouter. "
        f"Provenance on a gating decision must name the decider."
    )


@pytest.mark.asyncio
async def test_a_rule_based_refusal_does_not_claim_a_model_judged_it():
    """No citations means no model is consulted. Labelling that with a provider
    would be a new lie in place of the old one."""
    from app.services import rag_faithfulness_service as svc

    called = AsyncMock(return_value=(0.5, "should not run"))
    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_evaluate_via_ollama", called):
        result = await svc.evaluate("a case", [])

    assert result["score"] == 0.0
    assert result["evaluator"] == "no-citations", (
        f"a deterministic no-citations refusal is labelled "
        f"{result['evaluator']!r}, which implies a model rejected the case"
    )
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_ragas_strategy_is_still_named_ragas():
    """Ragas is genuinely its own evaluator, not a provider passthrough."""
    from app.services import rag_faithfulness_service as svc

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_resolve_backend", AsyncMock(return_value="ragas")), \
         patch.object(svc, "_evaluate_via_ragas",
                      AsyncMock(return_value=(0.8, "ok"))):
        result = await svc.evaluate("a case", ["a citation"])

    assert result["evaluator"] == "ragas"


@pytest.mark.asyncio
async def test_a_crashed_evaluator_still_names_the_provider():
    """The crash path used to report the strategy name too."""
    from app.services import rag_faithfulness_service as svc

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_resolve_backend", AsyncMock(return_value="ollama")), \
         patch.object(svc, "_active_llm_provider", AsyncMock(return_value="openrouter")), \
         patch.object(svc, "_evaluate_via_ollama",
                      AsyncMock(side_effect=RuntimeError("boom"))):
        result = await svc.evaluate("a case", ["a citation"])

    assert result["evaluator"] == "openrouter"
    assert "boom" in result["reason"]


@pytest.mark.asyncio
async def test_the_label_fits_the_column():
    """`faithfulness_evaluator` is String(30); a longer label would be silently
    truncated into something that names nothing."""
    from app.services import rag_faithfulness_service as svc

    with patch.object(svc, "_feature_enabled", AsyncMock(return_value=True)), \
         patch.object(svc, "_resolve_backend", AsyncMock(return_value="ollama")), \
         patch.object(svc, "_active_llm_provider",
                      AsyncMock(return_value="x" * 60)), \
         patch.object(svc, "_evaluate_via_ollama",
                      AsyncMock(return_value=(0.9, "ok"))):
        result = await svc.evaluate("a case", ["a citation"])

    case = _case()
    from app.services.rag_faithfulness_service import apply_evaluation
    apply_evaluation(case, result)
    assert len(case.faithfulness_evaluator) <= 30
