"""Ratchet: the confidence threshold is read through the gate, never from env.

The Settings > AI "Confidence Threshold" was honoured only by the confidence
gate and the action policy. Seven other readers took
``settings.AI_CONFIDENCE_THRESHOLD`` straight from the environment, one of them
(``triage_agent._AUTO_TRIAGE_CONFIDENCE``) as an import-time copy, so the plan,
the triage branch, the stages and the per-analysis ``threshold_check`` could
each use a different number, and an admin's change reached only some of them
(W04 AIC-03).

Every reader now takes the value the pipeline FROZE at start (state, else
``pipeline_ai_scope``), which falls back to ``confidence_gate.env_default_threshold()``.
This guard keeps a new direct read from creeping back: any
``<x>.AI_CONFIDENCE_THRESHOLD`` attribute read or ``getattr(<x>,
"AI_CONFIDENCE_THRESHOLD")`` in ``backend/app`` outside the allowlist fails.
Docstrings and comments do not count (the walk is over the AST).

Allowlist (the ratchet; shrink it, never grow it):
* ``core/config.py`` — the definition;
* ``services/ai_config_resolver.py`` — the env default of the effective config;
* ``services/confidence_gate.py`` — ``env_default_threshold()``, the one fallback;
* ``services/action_policy.py`` — a sync helper's documented fallback;
* ``routers/app_settings.py`` — the settings API's displayed default.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"
NAME = "AI_CONFIDENCE_THRESHOLD"
ALLOWED = frozenset({
    "core/config.py",
    "services/ai_config_resolver.py",
    "services/confidence_gate.py",
    "services/action_policy.py",
    "routers/app_settings.py",
})


def _reads(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == NAME:
            lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == NAME
        ):
            lines.append(node.lineno)
    return lines


def _offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        lines = _reads(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if lines and relative not in ALLOWED:
            found[relative] = lines
    return found


def test_no_confidence_threshold_read_outside_the_gate():
    offenders = _offenders()
    assert not offenders, (
        "settings.AI_CONFIDENCE_THRESHOLD read outside the gate; take the frozen pipeline "
        "value instead (pipeline_ai_scope.state_confidence_threshold / "
        f"current_confidence_threshold, or confidence_gate.resolve_confidence_threshold): {offenders}"
    )


def test_the_allowlist_names_real_files():
    """A renamed module must not leave a stale hole in the ratchet."""
    missing = sorted(entry for entry in ALLOWED if not (APP / entry).is_file())
    assert not missing, missing


def test_the_guard_sees_the_shapes_it_forbids():
    """The detector itself: both read shapes are caught, prose is not."""
    source = (
        '"""settings.AI_CONFIDENCE_THRESHOLD in a docstring is fine."""\n'
        "from app.core.config import settings\n"
        "A = settings.AI_CONFIDENCE_THRESHOLD\n"
        "B = getattr(settings, 'AI_CONFIDENCE_THRESHOLD', 80)\n"
        "# settings.AI_CONFIDENCE_THRESHOLD in a comment is fine too\n"
    )
    assert _reads(ast.parse(source)) == [3, 4]


def test_triage_has_no_import_time_threshold():
    """The constant that froze the env value at import is gone for good."""
    from app.agents import triage_agent

    assert not hasattr(triage_agent, "_AUTO_TRIAGE_CONFIDENCE")
