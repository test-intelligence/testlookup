"""W14: the agent-ops vocabularies the UI renders are the server's.

``frontend/src/types/agentOps.ts`` mirrors ``core/agent_ops_vocab``'s
``COMPARE_OUTPUT_STATES`` (the Compare tab's output line), ``BULK_JOB_STATES``
(when the bulk-job panel stops polling), ``WORKFLOW_DEFAULT_BASES`` (the bases a
"Use for new runs" switch can set) and ``agent_config_service.TIER_ORDER`` (the
deep run configuration's tier picker). A drifted value would leave the panel
polling forever, a switch PUTting a base the server refuses with 422, or a tier
the run configuration rejects. Read as TEXT (no Node in the backend suite),
like the other frontend-parity pins. Also pinned: the vocab module is pure (no
``app`` import) and the services mirror of ``TIER_ORDER`` is the one the
run-configuration sentence names.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core import agent_ops_vocab as vocab
from app.services.agent_config_service import TIER_ORDER

MODEL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "agentOps.ts"


def _source() -> str:
    if not MODEL.exists():
        pytest.fail(f"{MODEL} is missing: the parity this test pins has nothing to read")
    return MODEL.read_text(encoding="utf-8")


def _array_literal(name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source())
    assert match, f"{name} is not declared as an `as const` array literal in {MODEL.name}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


@pytest.mark.parametrize(
    ("name", "server"),
    [
        ("COMPARE_OUTPUT_STATES", vocab.COMPARE_OUTPUT_STATES),
        ("BULK_JOB_STATES", vocab.BULK_JOB_STATES),
        ("WORKFLOW_DEFAULT_BASES", vocab.WORKFLOW_DEFAULT_BASES),
        ("TIER_ORDER", TIER_ORDER),
    ],
)
def test_the_frontend_mirror_equals_the_server(name, server):
    assert _array_literal(name) == tuple(server)


def test_the_tier_sentence_names_every_tier_in_order():
    tiers = ", ".join(TIER_ORDER[:-1]) + " or " + TIER_ORDER[-1]
    assert vocab.RUN_CONFIG_TIER_INVALID == f"model_tier must be {tiers}."


def test_the_run_configuration_cap_and_its_sentence_agree():
    assert str(vocab.RUN_CONFIG_FAILURE_IDS_MAX) in vocab.RUN_CONFIG_TOO_MANY_FAILURES
    assert vocab.PRESCAN_MEMBERS_CAP == vocab.RUN_CONFIG_FAILURE_IDS_MAX


def test_the_default_bases_are_the_migrations_check():
    source = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
              / "0230_project_workflow_defaults.py").read_text(encoding="utf-8")
    quoted = ",".join(f"'{base}'" for base in vocab.WORKFLOW_DEFAULT_BASES)
    assert f"base IN ({quoted})" in source


def test_the_vocab_module_is_pure():
    tree = ast.parse(Path(vocab.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= {"__future__"}, imported
