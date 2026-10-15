"""Ratchet: nothing in ``backend/app`` reads ``settings.AI_TIMEOUT_SECONDS`` directly (AIC-02).

Settings > AI has a "Timeout (seconds)" field. Before W04 the value an admin
saved there reached no LLM call: every call site read the environment default,
most of them as an import-time module constant (``_LAYER_TIMEOUT_SECONDS =
min(120, settings.AI_TIMEOUT_SECONDS)``), so the saved value could not reach
them at all.

Now one rule decides it: ``pipeline_ai_scope.effective_llm_timeout`` (the value
frozen into the pipeline, tightened by an explicit project agent override and
the site's cap); for a caller with no pipeline state, the timeout ``get_llm``
stamped on the model it holds (``llm_factory.llm_call_timeout``) or, with no
model in hand, the live effective config
(``llm_factory.live_llm_timeout_seconds``). ``BudgetedLLM`` enforces the
stamped value on every provider call.

The env default may be read only where it is DEFINED or RESOLVED: the settings
model, the resolver that merges it with the stored value, the scope module that
falls back to it when nothing is frozen, and the settings router that shows it.
A new direct read anywhere else fails here (attribute access and ``getattr``
both count; comments and docstrings do not).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
NAME = "AI_TIMEOUT_SECONDS"

#: Where the env default may be read (paths relative to backend/app).
ALLOWED = frozenset({
    "core/config.py",
    "services/ai_config_resolver.py",
    "services/pipeline_ai_scope.py",
    "routers/app_settings.py",
})

#: The leaf call sites G-C moved onto the resolver; each must keep reading it.
LEAF_SITES = {
    "agents/summary_agent.py": "effective_llm_timeout",
    "agents/release_risk_agent.py": "effective_llm_timeout",
    "agents/regression_watchman.py": "effective_llm_timeout",
    "agents/conversation.py": "llm_call_timeout",
    "services/agent.py": "llm_call_timeout",
    "services/rag_generation_service.py": "live_llm_timeout_seconds",
    "services/llm_cluster_semaphore.py": "effective_llm_timeout",
    "services/llm_factory.py": "effective_llm_timeout",
}


def _reads(source: str) -> list[int]:
    """Line numbers of every code read of ``AI_TIMEOUT_SECONDS``."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == NAME:
            lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "hasattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == NAME
        ):
            lines.append(node.lineno)
    return lines


def offenders() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        if rel in ALLOWED:
            continue
        hits = _reads(path.read_text(encoding="utf-8"))
        if hits:
            found[rel] = hits
    return found


def test_the_scan_sees_a_read():
    """A scan that matched nothing would let the ratchet pass vacuously."""
    assert _reads("from app.core.config import settings\nx = min(90, settings.AI_TIMEOUT_SECONDS)\n") == [2]
    assert _reads("y = getattr(settings, 'AI_TIMEOUT_SECONDS', 90)\n") == [1]
    assert _reads("# settings.AI_TIMEOUT_SECONDS in a comment\n'''and AI_TIMEOUT_SECONDS here'''\n") == []
    # The allowed files really do read it (the allowlist is not stale).
    resolver = (APP / "services" / "ai_config_resolver.py").read_text(encoding="utf-8")
    assert _reads(resolver), "the resolver no longer reads the env default: shrink ALLOWED"


def test_no_llm_call_site_reads_the_env_timeout():
    found = offenders()
    assert not found, (
        "these files read settings.AI_TIMEOUT_SECONDS directly, so the Settings > AI "
        "timeout never reaches them. Use pipeline_ai_scope.effective_llm_timeout(state, "
        "agent_id=..., cap=...) inside a pipeline stage, or llm_factory.llm_call_timeout(llm) "
        "/ live_llm_timeout_seconds() for a caller with no pipeline state: "
        + ", ".join(f"{path}:{lines}" for path, lines in found.items())
    )


@pytest.mark.parametrize(("relative", "helper"), sorted(LEAF_SITES.items()))
def test_each_leaf_site_reads_the_resolver(relative, helper):
    source = (APP / relative).read_text(encoding="utf-8")
    assert helper in source, f"{relative} no longer resolves its timeout through {helper}"
