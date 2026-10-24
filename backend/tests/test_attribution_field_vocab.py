"""W11 REL-09: the attribution page's field fallback equals the server's vocabulary.

``GET /api/v1/projects/{project_id}/attribution-rules/fields`` names the fields
a rule can match on, from ``AttributionMatchField`` with the evaluator's glob
set. The page renders ``ATTRIBUTION_FIELD_FALLBACK``
(``frontend/src/types/attributionRules.ts``) until that answers. Two copies of
one vocabulary drift silently: a field the page offers that the server refuses
is a 422 on Save, and a help line that says "wildcards" for an exact field
writes rules that never fire. So the fallback is read here AS TEXT and held to
the route's own answer, value by value, label, glob flag and help text.
"""
from __future__ import annotations

import pathlib
import re
import uuid

import pytest

from app.models.postgres import AttributionMatchField
from app.routers import release_attribution_rules as router_mod

REPO_BACKEND = pathlib.Path(__file__).resolve().parent.parent
TYPES_TS = REPO_BACKEND.parent / "frontend" / "src" / "types" / "attributionRules.ts"

_ENTRY = re.compile(
    r"\{\s*value:\s*'([^']+)',\s*label:\s*'([^']+)',\s*glob:\s*(true|false),\s*"
    r"help:\s*\"([^\"]+)\"\s*,?\s*\}"
)


def _fallback() -> list[tuple[str, str, bool, str]]:
    if not TYPES_TS.exists():  # pragma: no cover - backend-only checkouts
        pytest.skip("frontend not present in this checkout")
    text = TYPES_TS.read_text(encoding="utf-8")
    start = text.index("export const ATTRIBUTION_FIELD_FALLBACK")
    end = text.index("]\n", start)
    block = text[start:end]
    entries = [(v, label, glob == "true", help_) for v, label, glob, help_ in _ENTRY.findall(block)]
    # Every `value:` in the block was parsed: a reformatted entry must fail here, not drop out.
    assert len(entries) == block.count("value:"), "an ATTRIBUTION_FIELD_FALLBACK entry no longer parses"
    return entries


def test_the_fallback_lists_exactly_the_enum_in_declaration_order():
    assert [e[0] for e in _fallback()] == [f.value for f in AttributionMatchField]


@pytest.mark.asyncio
async def test_the_fallback_says_what_the_fields_route_says():
    served = await router_mod.list_attribution_rule_fields(project_id=uuid.uuid4(), _=None, __=None)
    assert _fallback() == [(f.value, f.label, f.glob, f.help) for f in served.fields]
