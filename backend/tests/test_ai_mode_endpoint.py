"""GET /api/v1/settings/ai/mode — the analysis mode alone, for every signed-in role.

The sidebar (whether to offer Ask AI) and the chat, release-gate, run evidence
and pipeline pages read only ``analysis_mode``, for every role. They read it
from ``GET /settings/ai`` (QA lead and above), so a QA engineer, tester or
viewer got "Requires at least QA_LEAD role" toasts on every page and never saw
Ask AI (the UX redesign's browser E2E pass, 2026-10-07).

Pinned here: the route asks only for a signed-in user (no role), and it answers
the mode and nothing else of the config — no provider, model or key flag.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_BACKEND = Path(__file__).resolve().parents[1]


def test_the_mode_route_asks_for_a_signed_in_user_not_a_role():
    source = (REPO_BACKEND / "app" / "routers" / "app_settings.py").read_text(encoding="utf-8")
    block = re.search(
        r'@router\.get\(\s*"/ai/mode".*?async def get_ai_mode\((.*?)\)\s*->',
        source,
        re.DOTALL,
    )
    assert block, "GET /ai/mode route not found"
    assert "Depends(get_current_user)" in block.group(1)
    assert "require_role" not in block.group(1)


def test_the_full_config_route_stays_qa_lead_and_above():
    """The mode is opened up; the config (providers, models, key flags) is not."""
    source = (REPO_BACKEND / "app" / "routers" / "app_settings.py").read_text(encoding="utf-8")
    block = re.search(
        r'@router\.get\(\s*"/ai",.*?async def get_ai_config\((.*?)\)\s*->',
        source,
        re.DOTALL,
    )
    assert block, "GET /ai route not found"
    assert "require_role(UserRole.QA_LEAD)" in block.group(1)


@pytest.mark.asyncio
async def test_answers_the_mode_and_nothing_else(monkeypatch):
    from app.routers import app_settings

    async def _config(_db):
        return {"analysis_mode": "rules", "llm_provider": "openai", "openai_api_key": "sk-not-for-viewers"}

    monkeypatch.setattr(app_settings, "_load_ai_config", _config)
    result = await app_settings.get_ai_mode(_=SimpleNamespace(id="viewer"), db=SimpleNamespace())
    assert result.model_dump() == {"analysis_mode": "rules"}


@pytest.mark.asyncio
async def test_an_unset_mode_reads_auto(monkeypatch):
    from app.routers import app_settings

    async def _config(_db):
        return {}

    monkeypatch.setattr(app_settings, "_load_ai_config", _config)
    result = await app_settings.get_ai_mode(_=SimpleNamespace(id="viewer"), db=SimpleNamespace())
    assert result.analysis_mode == "auto"
