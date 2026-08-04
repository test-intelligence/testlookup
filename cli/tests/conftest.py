"""Make the CLI package importable when running ``pytest cli/tests`` from the
repo root (the CLI is a separate installable package with no repo-root
conftest to lean on)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
