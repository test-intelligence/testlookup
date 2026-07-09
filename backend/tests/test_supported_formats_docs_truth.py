"""
Docs-truth ratchet for supported ingestion formats (PMF backlog US-1.6).

The architecture README once advertised Robot/Cucumber ingestion for months
before any parser existed. This test makes that class of drift CI-blocking
in both directions: every format in ``_SUPPORTED_FORMATS`` must be named in
the user-facing docs and the CLI help, and the docs must not name a format
the backend doesn't accept.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.routers.ingest import _SUPPORTED_FORMATS

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_GUIDE = REPO_ROOT / "user-guide" / "getting-results-in.md"
CLI_UPLOAD = REPO_ROOT / "cli" / "testlookup_cli" / "commands" / "upload.py"

# 'auto' is a detection mode, not a format; the docs describe it separately.
_REAL_FORMATS = sorted(_SUPPORTED_FORMATS - {"auto"})


def test_user_guide_names_every_supported_format():
    text = USER_GUIDE.read_text(encoding="utf-8")
    missing = [f for f in _REAL_FORMATS if f"`{f}`" not in text]
    assert not missing, (
        f"user-guide/getting-results-in.md 'Supported formats' is missing: {missing}. "
        "Update the doc when adding a parser."
    )


def test_user_guide_does_not_advertise_unsupported_formats():
    text = USER_GUIDE.read_text(encoding="utf-8")
    # Backtick-quoted single words in the Supported formats section.
    section = text.split("## Supported formats", 1)[1].split("##", 1)[0]
    claimed = set(re.findall(r"`([a-z][a-z0-9_-]+)`", section))
    # Words that are legitimately backticked but aren't format keys.
    claimed -= {"auto", "--junitxml", "--json-report", "output.xml"}
    bogus = sorted(claimed - _SUPPORTED_FORMATS)
    assert not bogus, (
        f"user-guide/getting-results-in.md advertises formats the backend "
        f"does not accept: {bogus}"
    )


def test_cli_upload_help_names_every_supported_format():
    text = CLI_UPLOAD.read_text(encoding="utf-8")
    missing = [f for f in _REAL_FORMATS if f not in text]
    assert not missing, (
        f"cli upload --format help text is missing: {missing}. "
        "Update cli/testlookup_cli/commands/upload.py when adding a parser."
    )
