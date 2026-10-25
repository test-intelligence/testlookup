"""W15: the agent-action vocabularies the UI renders are the server's.

``frontend/src/types/agentActions.ts`` mirrors ``services/agent_action_types``'
``ACT_ACTION_TYPES`` (the Settings › AI agents › Act checkboxes: a drifted id
would PUT an allowlist the server refuses with 422), ``ACT_ACTION_LABELS`` (the
words of those checkboxes) and ``QUEUE_VIEWS`` (the ``?view=`` the hooks send:
a drifted view is a 422 on every Inbox load). It re-exports — never copies —
the reviews page's ``REVIEW_REASON_CODES`` (``types/review.ts``), which must be
the server's ``models.postgres.REVIEW_REASON_CODES`` (the Reject form's select:
a drifted code is a 422 ``reason_code_required``). Read as TEXT (no Node in the
backend suite), like the other frontend-parity pins.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.postgres import REVIEW_REASON_CODES
from app.services import agent_action_types as t

TYPES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "types"
ACTIONS = TYPES / "agentActions.ts"
REVIEW = TYPES / "review.ts"


def _source(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _array_literal(path: Path, name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _source(path))
    assert match, f"{name} is not declared as an `as const` array literal in {path.name}"
    return tuple(re.findall(r"'([^']*)'", match.group(1)))


def _record_literal(path: Path, name: str) -> dict[str, str]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{([^}}]*)\}}", _source(path))
    assert match, f"{name} is not declared as an object literal in {path.name}"
    return dict(re.findall(r"'([^']+)':\s*'([^']*)'", match.group(1)))


@pytest.mark.parametrize(
    ("name", "server"),
    [("ACT_ACTION_TYPES", t.ACT_ACTION_TYPES), ("QUEUE_VIEWS", t.QUEUE_VIEWS)],
)
def test_the_frontend_mirror_equals_the_server(name, server):
    assert _array_literal(ACTIONS, name) == tuple(server)


def test_the_act_labels_are_the_catalogues():
    assert _record_literal(ACTIONS, "ACT_ACTION_LABELS") == t.ACT_ACTION_LABELS


def test_the_reason_codes_are_re_exported_from_the_reviews_list_not_copied():
    source = _source(ACTIONS)
    assert re.search(
        r"export \{[^}]*\bREVIEW_REASON_CODES\b[^}]*\} from '\./review'", source
    ), "agentActions.ts must re-export REVIEW_REASON_CODES from ./review"
    assert "'unsupported_claim'" not in source, "agentActions.ts copies the reason codes"
    assert _array_literal(REVIEW, "REVIEW_REASON_CODES") == tuple(REVIEW_REASON_CODES)
    assert t.REVIEW_REASON_CODES == REVIEW_REASON_CODES
