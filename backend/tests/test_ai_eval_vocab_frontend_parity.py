"""W16: the AI-evaluation vocabularies the UI renders are the server's.

``frontend/src/types/aiEval.ts`` mirrors ``services/ai_eval_vocab.py``
(metric kinds and their labels, run / item statuses, candidate tiers, shadow
verdicts / label statuses / agent labels), ``eval_verdict.EvalVerdict`` (the
gate's verdict values — the Evaluation Gate tab compared ``'PASS'`` against
``pass`` and toasted "No baseline configured" for every run),
``eval_gate_service.DEFAULT_AGENT_STACK_GATES`` (the four gate buttons — the
duplicate-detection button asked ``ClusterAgent``, so a baseline set for the
stack's ``DefectPromotionAgent`` was never found) and
``tier_comparison_service.TIER_ORDER`` (the G2 tier selects: a drifted tier is
a 422). Read as TEXT (no Node in the backend suite), like the other
frontend-parity pins.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import ai_eval_vocab as v
from app.services.eval_gate_service import DEFAULT_AGENT_STACK_GATES
from app.services.eval_verdict import EvalVerdict
from app.services.tier_comparison_service import TIER_ORDER

AI_EVAL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "aiEval.ts"


def _source() -> str:
    if not AI_EVAL.exists():
        pytest.fail(f"{AI_EVAL} is missing: the parity this test pins has nothing to read")
    return AI_EVAL.read_text(encoding="utf-8")


def _array_literal(name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {AI_EVAL.name}"
    return match.group(1)


def _strings(name: str) -> tuple[str, ...]:
    return tuple(re.findall(r"'([^']*)'", _array_literal(name)))


def _record_literal(name: str) -> dict[str, str]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{([^}}]*)\}}", _source())
    assert match, f"{name} is not declared as an object literal in {AI_EVAL.name}"
    return dict(re.findall(r"'([^']+)':\s*'([^']*)'", match.group(1)))


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("METRIC_KINDS", v.METRIC_KINDS),
        ("RUN_STATUSES", v.RUN_STATUSES),
        ("TERMINAL_RUN_STATUSES", v.TERMINAL_RUN_STATUSES),
        ("ITEM_STATUSES", v.ITEM_STATUSES),
        ("CANDIDATE_TIERS", v.CANDIDATE_TIERS),
        ("SHADOW_VERDICTS", v.SHADOW_VERDICTS),
        ("SHADOW_LABEL_STATUSES", v.SHADOW_LABEL_STATUSES),
        ("TIER_ORDER", TIER_ORDER),
    ],
)
def test_the_frontend_array_equals_the_server(name, server):
    assert _strings(name) == tuple(server)


@pytest.mark.parametrize(
    ("name", "server"),
    [("METRIC_LABELS", v.METRIC_LABELS), ("SHADOW_AGENT_LABELS", v.SHADOW_AGENT_LABELS)],
)
def test_the_frontend_labels_equal_the_server(name, server):
    assert _record_literal(name) == server


def test_the_run_error_sentences_are_the_servers():
    """A sentence carries an apostrophe ("project's"), so its values are double-quoted literals."""
    match = re.search(r"export const RUN_ERROR_SENTENCES\b[^=]*=\s*\{([^}]*)\}", _source())
    assert match, f"RUN_ERROR_SENTENCES is not declared as an object literal in {AI_EVAL.name}"
    body = match.group(1)
    sentences = dict(re.findall(r"'([^']+)':\s*\"([^\"]*)\"", body))
    assert len(sentences) == body.count(":"), "every entry is a 'code': \"sentence\" literal"
    assert sentences == v.RUN_ERROR_SENTENCES
    assert {"run_error", "candidate_changed"} <= set(sentences), "the runner's two run-level codes have a sentence"


def test_the_verdict_labels_are_keyed_by_the_servers_verdict_values():
    labels = _record_literal("EVAL_VERDICT_LABELS")
    assert set(labels) == {verdict.value for verdict in EvalVerdict}
    assert labels == {"pass": "PASS", "fail": "FAIL", "insufficient_samples": "Not enough evidence"}


def test_the_shadow_verdict_buttons_cover_every_verdict():
    assert _record_literal("SHADOW_VERDICT_LABELS") == {
        "incumbent": "Incumbent correct",
        "candidate": "Candidate correct",
        "both": "Both correct",
        "neither": "Neither",
    }
    assert tuple(_record_literal("SHADOW_VERDICT_LABELS")) == v.SHADOW_VERDICTS


def test_the_default_gates_are_the_agent_stacks():
    body = _array_literal("DEFAULT_GATES")
    gates = re.findall(
        r"\{\s*task_type:\s*'([^']+)',\s*agent_name:\s*'([^']+)',\s*label:\s*'([^']+)'\s*\}", body
    )
    assert len(gates) == body.count("{"), "every gate is a {task_type, agent_name, label} literal"
    assert [{"task_type": t, "agent_name": a} for t, a, _ in gates] == [dict(g) for g in DEFAULT_AGENT_STACK_GATES]
    assert all(label.strip() for _, _, label in gates)
    assert {t for t, _, _ in gates} == set(v.TASK_TYPES)
