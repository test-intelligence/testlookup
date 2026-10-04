"""VIZ-607: the Summary Report exports carry their charts, and every export
leaves an audit row.

* The PDF draws a status pie, pass rate by suite and the top failing tests
  from the payload (``reportlab.graphics``, offline); a chart with nothing to
  show is left out. Read back with pypdf: the legend and bar labels are text.
* The workbook has a context sheet and one sheet per part of the report, each
  with a native Excel chart over its data; stored names cannot become
  formulas.
* ``/reports/summary/pdf`` and ``/xlsx`` write an ``AccessAuditLog`` row
  (reports.py always did; this route did not).
"""
from __future__ import annotations

import io
import re
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("reportlab")
openpyxl = pytest.importorskip("openpyxl")

from app.routers import summary_report as router  # noqa: E402
from app.services.summary_report_pdf import render_summary_report_pdf  # noqa: E402
from app.services.summary_report_xlsx import render_summary_report_xlsx  # noqa: E402

META = {
    "scope": {
        "projects": [{"id": "p1", "name": "Payment Service"}],
        "releases": [],
        "suites": ["payments"],
        "window": {"from": "2026-08-20", "to": "2026-09-19", "days": 30, "timezone": "UTC"},
    },
    "totals": {"matched_runs": 13, "total_runs": 13, "matched_executions": 182, "total_executions": 182},
    "pass_rate_basis": "unique_tests",
    "ignored_filters": [],
    "generated_at": "2026-09-19T17:57:27+00:00",
}


def _payload(**over) -> dict:
    payload = {
        "project_id": "p1",
        "project_name": "Payment Service",
        "mode": "window",
        "window_days": 30,
        "generated_at": "2026-09-19T17:57:27+00:00",
        "totals": {"total_test_cases": 129, "passed": 91, "failed": 28, "skipped": 9, "broken": 1, "evaluated": 120,
                   "pass_rate_pct": 70.5, "pass_rate_basis": "unique_tests", "pass_rate_basis_label": "per unique test",
                   "fail_rate_pct": 21.7, "skip_rate_pct": 7.0, "broken_rate_pct": 0.8, "weighted_pass_rate_pct": 75.8},
        "run_count": 13,
        "flaky_test_count": 4,
        "suites": [
            {"suite_name": "PaymentSuite", "total": 60, "passed": 40, "failed": 18, "skipped": 2, "broken": 0, "pass_rate_pct": 68.9},
            {"suite_name": "=HYPERLINK(\"http://evil\")", "total": 10, "passed": 10, "failed": 0, "skipped": 0, "broken": 0, "pass_rate_pct": 100.0},
        ],
        "top_failing_tests": [
            {"test_name": "test_refund_partial_amount", "suite_name": "PaymentSuite", "class_name": "RefundTest", "failures": 9},
            {"test_name": "+cmd|' /C calc'!A0", "suite_name": None, "class_name": None, "failures": 2},
        ],
        "meta": META,
    }
    payload.update(over)
    return payload


def _text(pdf: bytes) -> str:
    pypdf = pytest.importorskip("pypdf")
    return re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf)).pages))


class TestThePdfDrawsItsCharts:
    def test_the_three_charts_are_drawn_from_the_payload(self):
        text = _text(render_summary_report_pdf(_payload()))
        assert "Charts" in text
        for heading in ("Status breakdown", "Pass rate by suite, worst first", "Top failing tests"):
            assert heading in text
        # The pie's legend states each status with its count and share.
        for legend in ("Passed 91 (70.5%)", "Failed 28 (21.7%)", "Broken 1 (0.8%)", "Skipped 9 (7.0%)"):
            assert legend in text, legend
        # The bars carry their names and values.
        assert "PaymentSuite" in text and "69%" in text
        assert "test_refund_partial_amount" in text

    def test_a_chart_with_nothing_to_show_is_left_out(self):
        empty = _payload(
            totals={"total_test_cases": 0, "passed": 0, "failed": 0, "skipped": 0, "broken": 0},
            suites=[],
            top_failing_tests=[],
        )
        assert "Charts" not in _text(render_summary_report_pdf(empty))


class TestTheWorkbook:
    def _book(self, payload: dict):
        return openpyxl.load_workbook(io.BytesIO(render_summary_report_xlsx(payload)))

    def test_a_context_sheet_then_one_sheet_per_part_each_with_a_native_chart(self):
        book = self._book(_payload())
        assert book.sheetnames == ["Report context", "Totals", "Suites", "Top failing tests"]
        context = {row[0]: row[1] for row in book["Report context"].iter_rows(min_row=2, values_only=True) if row[0]}
        assert context["Project"] == "Payment Service"
        assert context["Test Suite"] == "payments"
        assert context["Aggregation"] == "All runs in window"
        assert any("Showing all 13 runs" in str(v) for v in context)
        for name in ("Totals", "Suites", "Top failing tests"):
            assert len(book[name]._charts) == 1, f"{name} has no chart"
        totals = {row[0]: row[1] for row in book["Totals"].iter_rows(min_row=2, max_row=5, values_only=True)}
        assert totals == {"Passed": 91, "Failed": 28, "Broken": 1, "Skipped": 9}

    def test_stored_names_cannot_become_formulas(self):
        book = self._book(_payload())
        suites = [c.value for c in book["Suites"]["A"][1:]]
        tests = [c.value for c in book["Top failing tests"]["A"][1:]]
        assert "'=HYPERLINK(\"http://evil\")" in suites
        assert "'+cmd|' /C calc'!A0" in tests
        for cell in [*book["Suites"]["A"][1:], *book["Top failing tests"]["A"][1:]]:
            assert cell.data_type == "s", f"{cell.coordinate} is a {cell.data_type}, not text"

    def test_no_data_means_no_chart_not_an_empty_one(self):
        book = self._book(_payload(totals={"passed": 0}, suites=[], top_failing_tests=[]))
        assert all(len(book[name]._charts) == 0 for name in ("Totals", "Suites", "Top failing tests"))


class _Db:
    def __init__(self):
        self.added: list = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["pdf", "xlsx"])
async def test_every_export_leaves_an_audit_row(monkeypatch, fmt):
    async def build(*_a, **_k):
        return _payload(meta=None)

    async def meta(*_a, **_k):
        return META

    monkeypatch.setattr(router.svc, "build_summary_report", build)
    monkeypatch.setattr(router, "build_meta", meta)
    scope = SimpleNamespace(
        project_id=uuid.uuid4(), window_days=30, release_arg=None, suite_arg="payments",
        release_ids=(), suite_names=("payments",),
    )
    user = SimpleNamespace(id=uuid.uuid4(), username="lead")
    db = _Db()
    response = await router._export(fmt, "window", scope, db, user)
    assert response.media_type == router._EXPORTS[fmt][2]
    assert f'filename="summary-payment_service-30d-window.{fmt}"' in response.headers["content-disposition"]
    rows = [obj for obj in db.added if type(obj).__name__ == "AccessAuditLog"]
    assert len(rows) == 1 and db.commits == 1
    assert rows[0].action == f"report_summary_export_{fmt}"
    assert rows[0].actor_user_id == user.id and rows[0].project_id == scope.project_id
    assert rows[0].after_value["suite_names"] == ["payments"]
