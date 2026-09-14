"""T10/E5.5 contract for the local SLM/LLM developer setup."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = ROOT / "Makefile"
SLM_TAG = "qwen2.5:3b-instruct-q5_K_M"
LLM_TAG = "qwen2.5:14b-instruct-q5_K_M"
EMBEDDING_TAG = "nomic-embed-text:v1.5"


def _recipe(source: str, target: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(target)}:[^\n]*\n(?P<body>(?:\t[^\n]*\n)+)",
        source,
    )
    assert match, f"Makefile target {target!r} is missing or has no recipe"
    return match.group("body")


def _assignment(source: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}\s*\?=\s*(\S+)\s*$", source)
    assert match, f"Makefile variable {name!r} must have an overridable default"
    return match.group(1)


def test_dev_llm_pulls_the_exact_tested_slm_and_llm_pair():
    source = MAKEFILE.read_text(encoding="utf-8")

    assert _assignment(source, "OLLAMA_SLM_MODEL") == SLM_TAG
    assert _assignment(source, "OLLAMA_LLM_MODEL") == LLM_TAG
    assert _assignment(source, "OLLAMA_EMBEDDING_MODEL") == EMBEDDING_TAG
    assert SLM_TAG != LLM_TAG

    dev_recipe = _recipe(source, "dev-llm")
    assert dev_recipe.index("--profile local-llm up -d --build") < dev_recipe.index(
        "$(MAKE) pull-llm"
    )

    pull_recipe = _recipe(source, "pull-llm")
    assert pull_recipe.count("ollama pull $(OLLAMA_SLM_MODEL)") == 1
    assert pull_recipe.count("ollama pull $(OLLAMA_LLM_MODEL)") == 1
    assert pull_recipe.count("ollama pull $(OLLAMA_EMBEDDING_MODEL)") == 1


def test_pull_llm_waits_for_ollama_and_is_safe_without_a_tty():
    pull_recipe = _recipe(MAKEFILE.read_text(encoding="utf-8"), "pull-llm")

    assert "until $(DOCKER_COMPOSE)" in pull_recipe
    assert "exec -T $(OLLAMA_CONTAINER) ollama list" in pull_recipe
    assert '"$$attempt" -ge 60' in pull_recipe
    assert "sleep 2" in pull_recipe
    assert pull_recipe.count("exec -T $(OLLAMA_CONTAINER) ollama pull") == 3


def test_local_llm_docs_name_the_same_exact_pair_and_automatic_pull():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    developer_guide = (ROOT / "architecture/DEVELOPER_GUIDE.md").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    for document in (readme, developer_guide):
        assert SLM_TAG in document
        assert LLM_TAG in document
        assert EMBEDDING_TAG in document
    assert "`make dev-llm` waits for Ollama and pulls" in readme
    assert "make pull-llm  # exact-tag 3B SLM + 14B LLM" in compose
    assert "make dev-llm` then `docker compose exec ollama ollama pull" not in readme


def test_fresh_install_defaults_reference_models_that_dev_llm_pulls():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    settings = (ROOT / "backend/app/core/config.py").read_text(encoding="utf-8")

    assert f"LLM_MODEL={SLM_TAG}" in env_example
    assert f"EMBEDDING_MODEL={EMBEDDING_TAG}" in env_example
    assert f'LLM_MODEL: str = "{SLM_TAG}"' in settings
    assert f'EMBEDDING_MODEL: str = "{EMBEDDING_TAG}"' in settings


def test_platform_bootstrap_scripts_pull_the_same_exact_pair():
    unix = (ROOT / "scripts/local_dev_setup_common.sh").read_text(encoding="utf-8")
    windows = (ROOT / "scripts/local_dev_setup_windows.ps1").read_text(
        encoding="utf-8"
    )

    for script in (unix, windows):
        assert SLM_TAG in script
        assert LLM_TAG in script
        assert EMBEDDING_TAG in script
        assert "qwen2.5:7b\n" not in script
    assert unix.count('ollama pull "${OLLAMA_') == 3
    assert windows.count("ollama ollama pull $ollama") == 3
    assert "COMPOSE_ARGS=(--profile local-llm)" in unix
    assert 'return "docker compose --profile local-llm"' in windows
