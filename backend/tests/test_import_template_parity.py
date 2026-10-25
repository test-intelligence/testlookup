"""W13 TCL-04: the CSV template the page downloads is the header list the server reads.

``frontend/src/pages/test-management/importCasesModel.ts`` (G-E) builds the
template client-side from ``IMPORT_TEMPLATE_HEADERS``; the server's copy lives
in ``core/test_library_vocab``. A drifted header would hand a person a template
whose column the import reports as "unknown" and drops. Read as TEXT (no Node
in the backend suite), like the other frontend-parity pins.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core.test_library_vocab import IMPORT_HEADERS, IMPORT_TEMPLATE_HEADERS

MODEL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "test-management" / "importCasesModel.ts"
_LITERAL = re.compile(r"export const IMPORT_TEMPLATE_HEADERS\s*=\s*(\[[^\]]*\])\s*as const", re.S)


def _frontend_headers() -> tuple[str, ...]:
    if not MODEL.exists():
        pytest.fail(f"{MODEL} is missing: the parity this test pins has nothing to read")
    match = _LITERAL.search(MODEL.read_text(encoding="utf-8"))
    assert match, "IMPORT_TEMPLATE_HEADERS is no longer one literal array in importCasesModel.ts"
    return tuple(ast.literal_eval(match.group(1)))


def test_the_template_headers_are_the_servers():
    assert _frontend_headers() == IMPORT_TEMPLATE_HEADERS


def test_every_template_header_is_one_the_import_reads():
    keys = [" ".join(h.split()).lower() for h in IMPORT_TEMPLATE_HEADERS]
    assert all(key in IMPORT_HEADERS for key in keys)
    assert sorted(set(IMPORT_HEADERS.values())) == sorted(IMPORT_HEADERS[key] for key in keys)
