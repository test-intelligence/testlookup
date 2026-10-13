"""Every LangChain integration the backend imports is installed by requirements.txt.

``llm_factory.get_llm()`` imports each provider's LangChain package lazily, inside
its branch, so a package missing from ``requirements.txt`` breaks only the
installs that select that provider, and only on the first AI call. That was
``anthropic``: the provider was accepted by the settings API, offered by the AI
settings page, priced and policy-checked, yet ``langchain-anthropic`` was never
declared, so ``LLM_PROVIDER=anthropic`` raised ``ModuleNotFoundError`` on every
image. The CI mypy run passes ``--ignore-missing-imports``, so the gap never
showed there either.

``test_llm_provider_vocabulary.py`` keeps policy, pricing and the UI in step with
the provider list; this file adds the missing consumer, the dependency list.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
REQUIREMENTS = BACKEND / "requirements.txt"

# Imported directly but installed by a declared distribution, not declared itself.
PROVIDED_BY = {"langchain-core": "langchain"}


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared() -> set[str]:
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(_canonical(re.split(r"[\[<>=!~;\s]", line, maxsplit=1)[0]))
    return names


def _langchain_imports() -> dict[str, list[str]]:
    """Distribution -> ``file:line`` sites for every ``langchain*`` import under app/."""
    sites: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                top = module.split(".", 1)[0]
                if top == "langchain" or top.startswith("langchain_"):
                    site = f"{path.relative_to(BACKEND).as_posix()}:{node.lineno}"
                    sites.setdefault(_canonical(top), []).append(site)
    return sites


def test_the_import_scan_finds_the_provider_packages():
    """Everything below compares against this scan; an empty one passes vacuously."""
    found = set(_langchain_imports())
    assert {"langchain-openai", "langchain-ollama", "langchain-anthropic"} <= found, found


def test_every_imported_langchain_package_is_declared():
    declared = _declared()
    assert "fastapi" in declared and "langchain" in declared, "requirements parse looks wrong"
    missing = {
        dist: sites
        for dist, sites in _langchain_imports().items()
        if dist not in declared and PROVIDED_BY.get(dist) not in declared
    }
    assert not missing, (
        f"imported under app/ but not installed by requirements.txt: {missing}; "
        "the provider fails with ModuleNotFoundError on first use"
    )


@pytest.mark.asyncio
async def test_the_anthropic_provider_builds_a_chat_model(monkeypatch):
    """The factory's keyword arguments reach ChatAnthropic, which the call's
    ``# type: ignore`` would otherwise hide. Runs where requirements.txt is
    installed (CI, the images); the declaration itself is checked above."""
    pytest.importorskip("langchain_anthropic")
    from langchain_anthropic import ChatAnthropic

    from app.core.config import settings
    from app.services.llm_factory import BudgetedLLM, get_llm

    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setattr(settings, "AI_MAX_RETRIES", 3)
    monkeypatch.setattr(settings, "LLM_TEMPERATURE", 0.2)
    monkeypatch.setattr(settings, "LLM_MAX_TOKENS", 1234)

    llm = await get_llm(provider="anthropic", model="claude-haiku-5-5")

    assert isinstance(llm, BudgetedLLM)
    chat = llm._inner
    assert isinstance(chat, ChatAnthropic)
    assert chat.model == "claude-haiku-5-5"
    assert chat.max_tokens == 1234
    assert chat.max_retries == 3
    assert chat.temperature == 0.2
    assert chat.anthropic_api_key.get_secret_value() == "sk-ant-test-not-a-real-key"
