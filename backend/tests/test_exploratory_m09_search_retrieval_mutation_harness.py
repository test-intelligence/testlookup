"""Run the M09 search-retrieval mutation proof in the normal test suite."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m09_search_retrieval_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "mutation_check_exploratory_m09_search_retrieval.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert "M09 search-retrieval mutation check: 11 mutations killed" in run.stdout
