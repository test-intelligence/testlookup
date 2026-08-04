"""Make the flat SDK modules (``testlookup_reporter``, ``ci_context``,
``commit_range``) importable when running ``pytest client/tests`` from the
repo root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
