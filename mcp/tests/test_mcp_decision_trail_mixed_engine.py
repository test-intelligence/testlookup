"""D-156: a stage whose tests ran on more than one engine prints its split.

The Root Cause stage stores ``analysis_mode="mixed"`` and the decision trail
carries the stage's ``mode_distribution``; the MCP trail prints both, so an
assistant never reads one model answer and three rules answers as "rules".
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

MCP_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MCP_DIR))

import client  # noqa: E402
from tools import decision_trail  # noqa: E402

RUN = "11111111-1111-4111-8111-111111111111"


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def _register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _register


def _trail(stage: dict) -> dict:
    return {
        "run_id": RUN, "pipeline_run_id": None, "pipeline_status": "completed",
        "total_cost_usd": 0, "total_tokens": 0, "stages": [stage],
        "workflow_events": [], "per_test": [], "mode_distribution": {}, "fallback_count": 0,
    }


def _render(monkeypatch, stage: dict) -> str:
    async def _get(path, params=None):
        return _trail(stage)

    monkeypatch.setattr(client, "get", _get)
    mcp = _FakeMCP()
    decision_trail.register(mcp)
    return asyncio.run(mcp.tools["get_decision_trail"](RUN))


def test_a_mixed_stage_prints_its_split(monkeypatch):
    out = _render(monkeypatch, {
        "stage_name": "root_cause_analysis", "status": "completed",
        "analysis_mode": "mixed", "mode_distribution": {"llm": 1, "rules": 3},
    })
    assert "- **Mode:** mixed (llm×1, rules×3)" in out


def test_a_hint_or_a_correction_is_not_a_second_engine(monkeypatch):
    """Review F6: a rules-only stage with a hint and a correction reads "rules"."""
    out = _render(monkeypatch, {
        "stage_name": "root_cause_analysis", "status": "completed",
        "analysis_mode": "rules", "mode_distribution": {"rules": 8, "hint": 1, "human_corrected": 1},
    })
    assert "- **Mode:** rules\n" in out + "\n"
    assert "mixed" not in out


def test_a_mixed_split_still_lists_the_correction(monkeypatch):
    out = _render(monkeypatch, {
        "stage_name": "root_cause_analysis", "status": "completed",
        "analysis_mode": "mixed", "mode_distribution": {"llm": 1, "rules": 2, "human_corrected": 1},
    })
    assert "- **Mode:** mixed (llm×1, rules×2, human_corrected×1)" in out


def test_a_single_engine_stage_prints_that_engine(monkeypatch):
    out = _render(monkeypatch, {
        "stage_name": "root_cause_analysis", "status": "completed",
        "analysis_mode": "rules", "mode_distribution": {"rules": 4},
    })
    assert "- **Mode:** rules\n" in out + "\n"
    assert "rules×4)" not in out
