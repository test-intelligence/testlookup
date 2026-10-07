"""CVE-2026-104873 is accepted in .trivyignore.yaml as NOT REACHABLE (langgraph-sdk).

The acceptance rests on two facts this test holds:

1. No code in the backend or the MCP server imports ``langgraph_sdk`` or
   langgraph's ``RemoteGraph`` (the only langgraph module that imports the SDK).
2. Importing the app's LangGraph workflows does not load ``langgraph_sdk``.

If either changes, the "not reachable" statement is false: upgrade langgraph
(the langchain 1.x migration) instead of extending the acceptance.
"""
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
SDK_IMPORT = re.compile(r"^\s*(from|import)\s+langgraph_sdk\b|RemoteGraph|langgraph\.pregel\.remote|pregel\s+import\s+remote", re.M)


def test_no_source_imports_the_langgraph_sdk_or_remote_graph():
    offenders = [
        str(path.relative_to(REPO))
        for root in (BACKEND / "app", REPO / "mcp")
        if root.exists()
        for path in root.rglob("*.py")
        if SDK_IMPORT.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert offenders == [], f"CVE-2026-104873 is accepted as unreachable; these import the SDK: {offenders}"


def _pinned_langgraph() -> str:
    m = re.search(r"^langgraph==([\w.]+)", (BACKEND / "requirements.txt").read_text(encoding="utf-8"), re.M)
    assert m, "langgraph is no longer pinned in backend/requirements.txt"
    return m.group(1)


def test_importing_the_workflows_does_not_load_the_sdk():
    # The claim is about the langgraph the image ships (the pin). A dev machine
    # with a newer langgraph (1.x imports the SDK from langgraph.runtime) is not
    # that image, so the check runs where the pin is installed: CI.
    installed = importlib.metadata.version("langgraph")
    if installed != _pinned_langgraph():
        pytest.skip(f"langgraph {installed} installed, the image ships {_pinned_langgraph()}")
    # A fresh interpreter: this test process may already hold anything.
    code = (
        "import sys\n"
        "import app.agents.workflow, app.agents.workflow_compiler, app.agents.investigator.workflow\n"
        "print('langgraph_sdk' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120, check=True
    ).stdout.strip().splitlines()[-1]
    assert out == "False", "importing the app's LangGraph workflows loaded langgraph_sdk"


def test_the_acceptance_is_still_there_for_this_test_to_guard():
    # When the langchain 1.x migration removes the entry, delete this file too.
    assert "CVE-2026-104873" in (REPO / ".trivyignore.yaml").read_text(encoding="utf-8")
