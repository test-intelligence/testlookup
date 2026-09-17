"""Prove M09 partial-result and project-switch consumer safeguards."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = (
    ROOT / ".venv311" / "Scripts" / "python.exe"
    if (ROOT / ".venv311" / "Scripts" / "python.exe").exists()
    else Path(sys.executable)
)
NPM = shutil.which("npm.cmd") or shutil.which("npm") or "npm"

MUTATIONS = [
    (
        ROOT / "frontend/src/pages/SearchPage.tsx",
        "frontend-hides-partial-warning",
        "      {response?.result_status === 'partial' && (\n",
        "      {false && response?.result_status === 'partial' && (\n",
        [NPM, "run", "test", "--", "--run", "src/pages/SearchPage.test.tsx", "-t", "labels capped"],
        ROOT / "frontend",
    ),
    (
        ROOT / "frontend/src/pages/SearchPage.tsx",
        "frontend-skips-result-project-switch",
        "      if (destinationProject) setActiveProject(destinationProject)\n",
        "      if (false && destinationProject) setActiveProject(destinationProject)\n",
        [NPM, "run", "test", "--", "--run", "src/pages/SearchPage.test.tsx", "-t", "selects a result project"],
        ROOT / "frontend",
    ),
    (
        ROOT / "cli/testlookup_cli/commands/search.py",
        "cli-hides-partial-warning",
        '        if data.get("result_status") == "partial":\n',
        '        if False and data.get("result_status") == "partial":\n',
        [str(PYTHON), "-m", "pytest", "-q", "-p", "no:testlookup", "tests/test_search_truthfulness.py"],
        ROOT / "cli",
    ),
    (
        ROOT / "mcp/tools/search.py",
        "mcp-claims-partial-empty-is-authoritative",
        "        if partial:\n            return (\n",
        "        if False and partial:\n            return (\n",
        [str(PYTHON), "-m", "pytest", "-q", "-p", "no:testlookup", "tests/test_mcp_search_truthfulness.py"],
        ROOT / "mcp",
    ),
]


def main() -> int:
    for target, name, good, bad, command, cwd in MUTATIONS:
        original = target.read_bytes()
        source = original.decode("utf-8")
        if source.count(good) != 1:
            raise AssertionError(f"{name} mutation must apply exactly once")
        try:
            target.write_text(source.replace(good, bad, 1), encoding="utf-8", newline="")
            run = subprocess.run(
                command, cwd=cwd, capture_output=True, text=True, check=False, timeout=120
            )
            if run.returncode != 1:
                raise AssertionError(
                    f"{name} mutation was not killed with exit 1\n{run.stdout}{run.stderr}"
                )
        finally:
            target.write_bytes(original)
        if target.read_bytes() != original:
            raise AssertionError(f"{name} mutation did not restore its target")

    print(f"M09 consumer mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
