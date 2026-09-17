"""Prove M08 distribution regressions reject unsafe mutations."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/app/routers/run_intelligence.py"
GOOD = """    if distribution.watermark:\n        # JSON exports cannot rely on a renderer to add the visible DRAFT\n        # banner. Carry the same watermark field as HTML/PDF exports so a\n        # downloaded pending report never loses its review status.\n        report[\"draft_watermark\"] = distribution.watermark\n"""
TEST = (
    "backend/tests/test_intelligence_export_review_envelope.py::"
    "test_allowed_pending_intelligence_export_carries_the_draft_watermark"
)


def main() -> int:
    original = TARGET.read_bytes()
    text = original.decode("utf-8")
    if text.count(GOOD) != 1:
        raise AssertionError("JSON draft-watermark mutation must apply exactly once")
    baseline = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            ".pytest-tmp-exploratory-m08-mutation-baseline",
            TEST,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if baseline.returncode != 0:
        raise AssertionError("mutation baseline failed\n" + baseline.stdout + baseline.stderr)
    try:
        TARGET.write_text(text.replace(GOOD, "", 1), encoding="utf-8", newline="")
        mutated = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:testlookup",
                "--basetemp",
                ".pytest-tmp-exploratory-m08-mutation-json-watermark",
                TEST,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if mutated.returncode != 1:
            raise AssertionError(
                "JSON draft-watermark mutation was not killed with pytest exit 1\n"
                + mutated.stdout
                + mutated.stderr
            )
    finally:
        TARGET.write_bytes(original)
    if TARGET.read_bytes() != original:
        raise AssertionError("mutation harness did not restore the source byte-for-byte")
    print("M08 distribution mutation check: 1 mutation killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
