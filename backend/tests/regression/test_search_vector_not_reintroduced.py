"""Nothing may reference ``search_vector`` — migration 0139 removed the column.

The ORM and the schema have to agree: a ``Mapped`` attribute for a column that
no longer exists produces ``UndefinedColumn`` on the first SELECT of that
entity, not at import, so it would pass every test that does not touch the DB.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("app.models.postgres")

from app.models.postgres import TestCase  # noqa: E402

pytestmark = pytest.mark.regression

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SEARCHED = ("backend/app", "cli", "mcp", "client", "frontend/src")


def test_the_orm_no_longer_maps_search_vector():
    assert not hasattr(TestCase, "search_vector"), (
        "TestCase still maps search_vector, but migration 0139 dropped the "
        "column — every query selecting this entity will raise UndefinedColumn"
    )

    index_names = {
        getattr(arg, "name", None) for arg in TestCase.__table_args__
    }
    assert "ix_test_cases_search" not in index_names, (
        "the tsvector GIN index is still declared on the model"
    )


def test_no_source_file_references_search_vector():
    """Guards against it being reintroduced piecemeal — a reader added without
    the column, or the column added back without a reader."""
    offenders = []
    scanned = 0
    for root in SEARCHED:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".java", ".go"}:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "search_vector" in text:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())

    # A scan that reaches nothing would pass while proving nothing.
    assert scanned > 100, f"only scanned {scanned} files — the globs are wrong"

    assert not offenders, (
        "these files reference search_vector, which migration 0139 dropped: "
        f"{offenders}. Either restore the column in a new migration or remove "
        "the reference."
    )
