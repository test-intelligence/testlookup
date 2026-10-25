"""The activity ledger's CSV export neutralises formula cells (W13 §0.29, D-15).

``GET /projects/{project_id}/activity/export?format=csv`` wrote every cell raw
(``csv.DictWriter`` over the stored values) while the product's other CSV
writers neutralise a formula through ``services/csv_safe`` (EXEC-06). W13 puts
NEW person-typed text into ledger rows -- an import's file name
(``entity_label``), requirement keys (``summary``), suite names on a rename --
so a label ``=HYPERLINK("http://x")`` must open in a spreadsheet as that text.

Driven through the real ``_export_row`` (the ORM row -> export dict step) and
``rows_to_csv``; the header, the column order and a benign row stay exactly
what the old writer produced.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.activity import query as q

HEADER = (
    "occurred_at,category,event_type,actor_type,actor_name,entity_type,entity_id,entity_label,summary,"
    "release_id,source_table,source_id"
)


def _event(**overrides) -> SimpleNamespace:
    row = {
        "occurred_at": datetime(2026, 10, 24, 9, 30, tzinfo=timezone.utc),
        "category": "test_management",
        "event_type": "test_case.imported",
        "actor_type": "user",
        "actor_name": "Priya QA",
        "entity_type": "test_library",
        "entity_id": str(uuid.UUID(int=7)),
        "entity_label": "cases.csv",
        "summary": "Imported 17 test cases from cases.csv",
        "release_id": None,
        "source_table": None,
        "source_id": None,
        **overrides,
    }
    return SimpleNamespace(**row)


def _cells(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def _raw(rows: list[dict]) -> str:
    """What the writer produced before W13 (every cell raw)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=HEADER.split(","))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def test_a_formula_label_is_exported_as_text():
    label = '=HYPERLINK("http://x")'
    rows = [q._export_row(_event(entity_label=label, summary=f"Imported 3 test cases from {label}"))]
    (cells,) = _cells(q.rows_to_csv(rows))
    assert cells["entity_label"] == "'" + label
    assert cells["summary"] == f"Imported 3 test cases from {label}", "a formula mid-text is inert already"


@pytest.mark.parametrize(
    "typed",
    ["+cmd|' /C calc'!A0", "@SUM(1+1)", "-2+3", "\t=1+1", "\r=1+1", "＝1+1"],
)
def test_every_formula_lead_is_neutralised_in_every_text_column(typed):
    rows = [q._export_row(_event(actor_name=typed, entity_label=typed, summary=typed))]
    (cells,) = _cells(q.rows_to_csv(rows))
    for column in ("actor_name", "entity_label", "summary"):
        assert cells[column].startswith("'"), (column, cells[column])


def test_a_plain_negative_number_stays_a_number():
    rows = [q._export_row(_event(entity_label="-5"))]
    (cells,) = _cells(q.rows_to_csv(rows))
    assert cells["entity_label"] == "-5"


def test_the_header_the_column_order_and_a_benign_row_are_unchanged():
    rows = [
        q._export_row(_event()),
        q._export_row(_event(release_id=uuid.UUID(int=3), source_table="audit_logs", source_id=uuid.UUID(int=4))),
    ]
    text = q.rows_to_csv(rows)
    assert text.splitlines()[0] == HEADER
    assert text == _raw(rows), "a row with nothing to neutralise is byte-identical to the old export"
    first, second = _cells(text)
    assert first["occurred_at"] == "2026-10-24T09:30:00+00:00"
    assert first["release_id"] == "" and first["source_id"] == ""
    assert second["release_id"] == str(uuid.UUID(int=3)) and second["source_table"] == "audit_logs"
