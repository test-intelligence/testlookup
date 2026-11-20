"""The run export's file name is built from a sanitised build number and is quoted.

The rule: a build number is text chosen by whoever uploaded the run. In the
``Content-Disposition`` header of ``GET /runs/{run_id}/export`` it is reduced
to ``[A-Za-z0-9._-]`` and the file name is quoted, so it can add no header
parameter, cannot break the header (a quote, a line break) and cannot make
the header impossible to encode (a character outside latin-1 used to answer
500 for everyone exporting that run).

The same route also answers for a run that has no AI summary: a stored
``structured_summary`` of ``None`` used to raise ``AttributeError`` (500).
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.routers import run_intelligence
from app.services.review_envelope import ReviewEnvelope

pytestmark = pytest.mark.regression

RUN_ID = uuid.UUID("0f3e9c1b-2a4d-4c6e-8f10-123456789abc")
LINE_BREAK = chr(13) + chr(10)
PROJECT_ID = str(uuid.uuid4())


def _intelligence(**overrides) -> dict:
    payload: dict = {
        "run": {"build_number": "42", "project_id": str(uuid.uuid4())},
        "structured_summary": {"executive_summary": "verified", "decision_report": {"report_id": "r-1"},
                               "decision_report_verification": {"status": "passed"}},
        "what_changed_since_last_good_run": None,
        "release_decision": None,
        "failure_clusters": [],
        "dimension_scores": [],
        "defect_candidates": [],
        "role_actions": {},
        "provenance": {},
        "category_breakdown": {},
    }
    payload.update(overrides)
    return payload


async def _export(monkeypatch, intelligence: dict):
    monkeypatch.setattr(run_intelligence, "get_mongo_db", object)
    monkeypatch.setattr(run_intelligence, "get_run_intelligence", AsyncMock(return_value=intelligence))
    monkeypatch.setattr(run_intelligence, "get_run_mode_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(run_intelligence, "review_envelope_for_run", AsyncMock(return_value=ReviewEnvelope(
        ai_generated=True, state="accepted", message="Reviewed and accepted.",
    )))
    monkeypatch.setattr(
        run_intelligence, "decide_run_distribution",
        AsyncMock(return_value=SimpleNamespace(allowed=True, watermark=None)),
    )
    monkeypatch.setattr(run_intelligence, "record_distribution", AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    return await run_intelligence.export_intelligence_report(RUN_ID, db=db)


def _disposition(response) -> str:
    return response.headers["content-disposition"]


@pytest.mark.parametrize("build_number, expected", [
    # An extra header parameter: the ';', the '*', the '=' and the quotes are gone.
    ("1; filename*=UTF-8''x.html", "intelligence-report-1_filename_UTF-8_x.html.json"),
    # A quote and a line break cannot end the value or start a header.
    ('7"' + LINE_BREAK + "X-Audit-Added: yes", "intelligence-report-7_X-Audit-Added_yes.json"),
    # Outside latin-1: used to make the header impossible to encode (500).
    ("build-" + chr(0x4E2D) + chr(0x6587) + "-9", "intelligence-report-build-_-9.json"),
    # A path separator cannot reach the saved name.
    ("release/2026\\rc1", "intelligence-report-release_2026_rc1.json"),
])
async def test_an_unusual_build_number_cannot_shape_the_header(monkeypatch, build_number, expected):
    response = await _export(monkeypatch, _intelligence(run={"build_number": build_number, "project_id": PROJECT_ID}))

    assert _disposition(response) == f'attachment; filename="{expected}"'
    # The header is one quoted parameter and is encodable as sent on the wire.
    assert _disposition(response).count('"') == 2
    assert dict(response.raw_headers)[b"content-disposition"].decode("latin-1") == _disposition(response)


@pytest.mark.parametrize("run", [{"build_number": ""}, {"build_number": None}, {}, {"build_number": ";;;"}])
async def test_a_missing_build_number_falls_back_to_the_run_id(monkeypatch, run):
    response = await _export(monkeypatch, _intelligence(run={**run, "project_id": PROJECT_ID}))

    assert _disposition(response) == 'attachment; filename="intelligence-report-0f3e9c1b.json"'


async def test_a_plain_build_number_keeps_its_name(monkeypatch):
    response = await _export(monkeypatch, _intelligence(run={"build_number": "audit-plain-7", "project_id": PROJECT_ID}))

    assert _disposition(response) == 'attachment; filename="intelligence-report-audit-plain-7.json"'
    assert json.loads(response.body)["summary"] == "verified"


@pytest.mark.parametrize("summary", [None, {}])
async def test_a_run_without_an_ai_summary_still_exports(monkeypatch, summary):
    response = await _export(monkeypatch, _intelligence(structured_summary=summary))

    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["summary"] is None
    assert body["decision_report"] is None
    assert body["decision_report_verification"] is None
    assert _disposition(response) == 'attachment; filename="intelligence-report-42.json"'


async def test_a_run_with_an_ai_summary_exports_it(monkeypatch):
    body = json.loads((await _export(monkeypatch, _intelligence())).body)

    assert body["summary"] == "verified"
    assert body["decision_report"] == {"report_id": "r-1"}
    assert body["decision_report_verification"] == {"status": "passed"}
