from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_agent_step_tracing_mutation_harness():
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "mutation_check_e2_1.py")],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "E2.1 mutation check: 15 mutations killed"
