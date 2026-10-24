"""W12 SRCH-04: the page's Syntax help says what the server applies.

The /search page's **Syntax** table (``frontend/src/components/search/searchSyntax.ts``)
lists each filter key and the kinds it applies to; the server's truth is
``core/search_vocab.FILTER_KEYS`` / ``FILTER_SUPPORT`` (an entity type that
cannot apply a key is NOT searched). A table that drifted would tell a person
a filter applies where it silently empties the results. Read as TEXT (no
Node in the backend suite), like the other frontend-parity pins.

Also pinned: the page's scope chips (``SearchPage.tsx`` ``SCOPES``, and the
model's ``ENTITY_SCOPES``) are exactly ``SEARCH_SCOPES``, each mapped to the
server's entity type.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.search_vocab import ENTITY_LABELS, ENTITY_TYPES, FILTER_KEYS, FILTER_SUPPORT, SCOPE_ENTITY, SEARCH_SCOPES
from app.services.search_query_parser import parse_query

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
SYNTAX = FRONTEND / "components" / "search" / "searchSyntax.ts"
SEARCH_PAGE = FRONTEND / "pages" / "SearchPage.tsx"
SEARCH_MODEL = FRONTEND / "components" / "search" / "searchModel.ts"

_ROW = re.compile(
    r"\{\s*key:\s*'(?P<key>[a-z]+)',\s*example:\s*'(?P<example>[^']*)',\s*appliesTo:\s*\[(?P<applies>[^\]]*)\]\s*\}"
)


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _syntax_rows() -> list[dict]:
    source = _read(SYNTAX)
    # The array closes at the first line that is only "]"; take every row before it.
    block = source.split("SEARCH_SYNTAX", 1)[1]
    block = block[: block.index("\n]")]
    rows = []
    for match in _ROW.finditer(block):
        applies = re.findall(r"'([a-z_]+)'", match.group("applies"))
        rows.append({"key": match.group("key"), "example": match.group("example"), "applies": applies})
    return rows


def test_the_syntax_table_lists_every_key_in_the_server_order():
    assert [row["key"] for row in _syntax_rows()] == list(FILTER_KEYS)


@pytest.mark.parametrize("key", FILTER_KEYS)
def test_each_key_applies_to_exactly_the_kinds_the_server_applies_it_to(key):
    [row] = [r for r in _syntax_rows() if r["key"] == key]
    server = [entity for entity in ENTITY_TYPES if key in FILTER_SUPPORT[entity]]
    assert sorted(row["applies"]) == sorted(server), (
        f"the Syntax table says {key}: applies to {row['applies']}, the server applies it to {server}"
    )


@pytest.mark.parametrize("key", FILTER_KEYS)
def test_each_example_is_a_filter_the_parser_applies(key):
    [row] = [r for r in _syntax_rows() if r["key"] == key]
    parsed = parse_query(row["example"], now=datetime(2026, 10, 24, tzinfo=timezone.utc))
    assert [f.key for f in parsed.filters] == [key], f"the example {row['example']!r} is not a {key}: filter"
    assert parsed.ignored == ()


def test_the_entity_labels_match():
    source = _read(SYNTAX)
    block = source.split("SYNTAX_ENTITY_LABELS", 1)[1].split("}", 1)[0]
    labels = dict(re.findall(r"([a-z_]+):\s*'([^']+)'", block))
    assert labels == ENTITY_LABELS


def test_the_page_scope_chips_are_the_server_scopes():
    source = _read(SEARCH_PAGE)
    block = source.split("const SCOPES", 1)[1]
    block = block[: block.index("\n]")]
    rows = re.findall(r"\{\s*id:\s*'([a-z]+)',[^}]*entityKey:\s*(?:'([a-z_]+)'|null)", block)
    assert [scope for scope, _ in rows] == list(SEARCH_SCOPES)
    assert {scope: entity for scope, entity in rows if entity} == SCOPE_ENTITY


def test_the_model_scopes_are_the_server_scopes():
    source = _read(SEARCH_MODEL)
    match = re.search(r"ENTITY_SCOPES\s*=\s*\[([^\]]*)\]", source)
    assert match, "searchModel.ts no longer declares ENTITY_SCOPES"
    assert re.findall(r"'([a-z]+)'", match.group(1)) == list(SEARCH_SCOPES)
