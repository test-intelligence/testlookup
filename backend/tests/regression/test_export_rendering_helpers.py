"""The helpers that decide what lands in an exported document had never run.

``routers/test_management_exports.py`` measures **19.6% statement coverage**,
and it is the module that held **five of the nine cross-tenant IDORs** fixed in
#905. Its existing guard,
``tests/regression/test_test_management_export_scope.py``, is deliberately
source-level: it pins that each by-id handler resolves project scope, and says
why ("a mocked request test would assert the mock").

That leaves the other half unchecked. Reading the coverage data per endpoint,
all five document exports — Excel, plan Word/PDF, strategy Word/PDF — show
exactly **one executed line each: the ``def``**. Their bodies have never run,
so nothing has ever exercised the code that turns a stored plan into the file a
human downloads and believes.

These five helpers are the whole rendering vocabulary of that path. They are
pure, so they need no request and no mock, and their bugs are silent: a
mangled field does not raise, it just produces a document that quietly says
something else.

``_pdf_text`` is the one with teeth — it escapes free text before it reaches a
reportlab ``Paragraph``, which parses a mini-markup. The strings it escapes are
AI-generated plan content, so an unescaped ``<`` is both a corrupt document and
an injection into a renderer.
"""
from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import pytest

from app.routers import test_management_exports
from app.routers.test_management_exports import (
    _list_to_str,
    _normalize_dict_list,
    _normalize_list,
    _pdf_text,
    _safe,
)

pytestmark = pytest.mark.regression


class _ExportResult:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._rows


class _PlanExportDB:
    def __init__(self, plan):
        self._results = [_ExportResult(scalar=plan), _ExportResult(rows=[])]

    async def execute(self, _statement):
        return self._results.pop(0)


class _CaseExportDB:
    def __init__(self, test_case):
        self._result = _ExportResult(rows=[test_case])

    async def execute(self, _statement):
        return self._result


class TestSafe:
    def test_none_becomes_empty_not_the_string_none(self):
        """The whole point: a missing field must not print "None" in a document
        the reader takes as a record."""
        assert _safe(None) == ""

    @pytest.mark.parametrize("value,expected", [(0, "0"), (False, "False"), ("", "")])
    def test_falsy_values_that_are_not_none_survive(self, value, expected):
        """0 is a measurement; None is its absence. Only the second is blank."""
        assert _safe(value) == expected


class TestListToStr:
    def test_a_list_becomes_one_bullet_per_line(self):
        assert _list_to_str(["a", "b"]) == "• a\n• b"

    def test_non_string_items_are_stringified(self):
        assert _list_to_str([1, 2]) == "• 1\n• 2"

    def test_a_bare_string_passes_through_unbulleted(self):
        assert _list_to_str("already text") == "already text"

    def test_empty_and_none_render_as_empty(self):
        assert _list_to_str([]) == ""
        assert _list_to_str(None) == ""

    def test_a_falsy_scalar_is_dropped_entirely(self):
        """DOCUMENTED DEFECT, pinned so a fix is a deliberate change.

        The guard is ``if not items``, so 0 and False take the empty branch and
        vanish from the document -- while the sibling ``_safe(0)`` correctly
        renders "0". A reader cannot tell "the value was zero" from "there was
        no value", which is the same absence-vs-zero confusion this codebase
        has fixed twice elsewhere (``/health/ingestion``, the Library Health
        panel).

        Low blast radius today: the call sites pass list-shaped fields. If one
        ever passes a scalar count, this is where it disappears.
        """
        assert _list_to_str(0) == ""
        assert _list_to_str(False) == ""
        # The asymmetry itself, so the two cannot silently diverge further.
        assert _safe(0) == "0"


class TestPdfText:
    """Escaping before reportlab's Paragraph, which parses markup."""

    def test_markup_characters_are_escaped(self):
        assert _pdf_text("<b>&") == "&lt;b&gt;&amp;"

    def test_a_paragraph_tag_cannot_be_injected(self):
        """Plan content is AI-generated free text. Unescaped, a stray tag is
        both a corrupt document and an injection into the renderer."""
        out = _pdf_text('</para><para fontSize="72">HUGE')
        assert "<para" not in out
        assert "&lt;/para&gt;" in out

    def test_newlines_become_line_breaks_after_escaping(self):
        """Order matters: escape first, then insert real <br/> tags. Escaping
        afterwards would neuter the breaks into visible text."""
        assert _pdf_text("a\nb") == "a<br/>b"

    def test_a_newline_next_to_markup_gets_both_treatments(self):
        assert _pdf_text("<i>\nx") == "&lt;i&gt;<br/>x"

    def test_none_is_empty_rather_than_the_word_none(self):
        assert _pdf_text(None) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "status", "description", "objective"])
async def test_plan_pdf_escapes_every_free_text_paragraph(monkeypatch, field):
    """Stored plan text must not be parsed as ReportLab paragraph markup."""
    pytest.importorskip("reportlab")

    plan = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="Plan",
        status="draft",
        description="Description",
        objective="Objective",
        total_cases=0,
        executed_cases=0,
        passed_cases=0,
        failed_cases=0,
        blocked_cases=0,
    )
    setattr(plan, field, "<b>")

    async def allow_scope(*_args, **_kwargs):
        return plan.project_id, None

    monkeypatch.setattr(test_management_exports, "resolve_project_scope", allow_scope)
    response = await test_management_exports.export_test_plan_pdf(
        plan.id,
        db=_PlanExportDB(plan),
        current_user=object(),
    )

    assert response.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_excel_export_serializes_stored_text_as_data_not_formulas(monkeypatch):
    """Opening an export must not execute formula-looking test-case content."""
    openpyxl = pytest.importorskip("openpyxl")
    from app.core import deps

    formula = '=HYPERLINK("https://example.invalid", "click")'
    test_case = SimpleNamespace(
        title=formula,
        status="draft",
        test_type="functional",
        priority="medium",
        severity="major",
        feature_area=formula,
        objective=formula,
        preconditions=formula,
        expected_result=formula,
        is_automated=False,
        automation_status="not_automated",
        ai_generated=False,
        ai_quality_score=None,
        last_execution_status=None,
        tags=[formula],
        steps=None,
        version=1,
        created_at=None,
    )
    project_id = uuid.uuid4()

    async def allow_scope(*_args, **_kwargs):
        return project_id, None

    monkeypatch.setattr(deps, "resolve_project_scope", allow_scope)
    response = await test_management_exports.export_test_cases_excel(
        project_id=project_id,
        db=_CaseExportDB(test_case),
        current_user=object(),
    )

    workbook = openpyxl.load_workbook(io.BytesIO(response.body), data_only=False)
    sheet = workbook["Test Cases"]
    for column in (1, 6, 7, 8, 10, 16):
        cell = sheet.cell(row=2, column=column)
        assert cell.data_type != "f", f"column {column} exported executable formula"
        assert cell.value == "'" + formula


class TestNormalizeList:
    """Best-effort normalisation of AI-generated JSON fields — the messiest
    input in the export path, and the branchiest function in the module."""

    @pytest.mark.parametrize("value", [None, [], "", "   "])
    def test_nothing_shaped_input_yields_an_empty_list(self, value):
        assert _normalize_list(value) == []

    def test_a_list_is_returned_as_is(self):
        assert _normalize_list(["a"]) == ["a"]

    def test_a_tuple_becomes_a_list(self):
        assert _normalize_list(("a", "b")) == ["a", "b"]

    def test_a_bare_dict_is_wrapped_not_flattened_to_its_keys(self):
        """`list({"k": 1})` would give ["k"] and silently lose the value."""
        assert _normalize_list({"k": 1}) == [{"k": 1}]

    def test_a_json_array_string_is_parsed(self):
        assert _normalize_list("[1, 2]") == [1, 2]

    def test_a_json_object_string_is_parsed_and_wrapped(self):
        assert _normalize_list('{"a": 1}') == [{"a": 1}]

    def test_malformed_json_falls_back_to_the_raw_text(self):
        """The fallback is the point: an LLM emitting nearly-JSON must still
        put its content in the document rather than raising or vanishing."""
        assert _normalize_list("{bad json") == ["{bad json"]

    def test_a_markdown_bullet_list_is_split_and_stripped(self):
        assert _normalize_list("- one\n- two") == ["one", "two"]

    def test_plain_prose_is_kept_as_a_single_entry(self):
        assert _normalize_list("not json") == ["not json"]

    def test_a_scalar_is_wrapped(self):
        assert _normalize_list(5) == [5]


class TestNormalizeDictList:
    def test_dicts_pass_through(self):
        assert _normalize_dict_list([{"x": 1}]) == [{"x": 1}]

    def test_non_dicts_are_wrapped_under_value(self):
        """Table renderers index by key; a bare scalar would raise or render
        blank without this."""
        assert _normalize_dict_list([{"x": 1}, 2]) == [{"x": 1}, {"value": 2}]

    def test_it_inherits_the_string_normalisation(self):
        assert _normalize_dict_list("- a") == [{"value": "a"}]

    def test_nothing_shaped_input_yields_an_empty_list(self):
        assert _normalize_dict_list(None) == []
