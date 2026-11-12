"""Every ReAct triage tool has a friendly Investigation Trail label.

Found on the reference K3s install (2026-11-03, Mobile Banking App run 742d41b1): the AI
analysis trail showed a lower-case step "recall similar failures" between
"Analysed OpenShift pod events" and "2 evidence items". The panel labels each
``tools_used`` entry with ``TOOL_LABELS[tool] ?? tool.replace(/_/g, ' ')``, and
``recall_similar_failures`` joined the agent's registry without a label, so it
fell through to its raw name.

This pins the registry the agent actually binds (``_get_tools()``, with every
tool allowed) against the keys of ``TOOL_LABELS`` in the panel source.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services import agent

REPO = Path(__file__).resolve().parents[3]
PANEL = REPO / "frontend" / "src" / "components" / "ai" / "AIAnalysisPanel.tsx"


def _tool_label_keys() -> set[str]:
    source = PANEL.read_text(encoding="utf-8")
    block = re.search(r"const TOOL_LABELS: Record<string, string> = \{(.*?)\n\}", source, re.S)
    assert block, f"TOOL_LABELS not found in {PANEL}"
    return set(re.findall(r"^\s*([a-z_]+):", block.group(1), re.M))


def test_every_react_tool_has_a_trail_label(monkeypatch):
    monkeypatch.setattr(agent, "tool_allowed", lambda _name: True)
    registered = {tool.name for tool in agent._get_tools()}

    assert "recall_similar_failures" in registered
    missing = sorted(registered - _tool_label_keys())
    assert not missing, f"add TOOL_LABELS entries in {PANEL.name} for: {missing}"
