"""Keep E9.8's source-level scorer mutation proof in CI."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_every_eval_scorer_mutation_is_killed() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_e9_8.py")],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "E9.8 mutation check: 29 mutations killed"
