"""Keep the M07 review-authority mutation proof wired into the backend suite."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_m07_review_authority_mutation_harness() -> None:
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "mutation_check_exploratory_m07_review_authority.py"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=720,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "M07 review-authority mutation check: 13 mutations killed"
