"""W28 TCM-01: masked parameter values never leave the Test Management API, and
an edit that sends a masked row back without its value keeps the secret.

``/test-management/cases`` returned authored parameter values raw, masked ones
included, while the canonical detail masked them. Masking at the boundary is
only half of it: the edit form cannot prefill a value it never received, so
without the merge every save of an edited case would erase its secrets.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.test_case_parameters import mask_parameters, merge_masked_parameters


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, None),
        ("not a list", None),
        ([], []),
        (
            [{"name": "token", "value": "s3cr3t", "masked": True}],
            [{"name": "token", "value": None, "masked": True}],
        ),
        (
            [{"name": "pin", "value": "1234", "mode": "hidden"}],
            [{"name": "pin", "value": None, "mode": "hidden", "masked": True}],
        ),
        (
            [{"name": "pw", "value": "x", "mode": "MASKED", "masked": False}],
            [{"name": "pw", "value": None, "mode": "MASKED", "masked": True}],
        ),
        (
            [{"name": "region", "value": "eu", "masked": False}],
            [{"name": "region", "value": "eu", "masked": False}],
        ),
        (
            ["loose string", 7, {"name": "env", "value": "staging"}],
            [{"name": "env", "value": "staging"}],
        ),
    ],
    ids=["none", "non-list", "empty", "masked-true", "mode-hidden", "mode-masked", "plain", "non-dict-dropped"],
)
def test_mask_parameters_case_table(stored, expected):
    assert mask_parameters(stored) == expected


def test_mask_parameters_copies_and_never_mutates_the_stored_rows():
    stored = [{"name": "token", "value": "s3cr3t", "masked": True}]
    masked = mask_parameters(stored)
    assert masked is not None and masked[0] is not stored[0]
    assert stored == [{"name": "token", "value": "s3cr3t", "masked": True}]


STORED = [
    {"name": "token", "value": "s3cr3t", "masked": True},
    {"name": "region", "value": "eu", "masked": False},
    {"name": "pin", "value": "1234", "mode": "hidden"},
]


def test_a_masked_row_sent_back_without_a_value_keeps_the_stored_secret():
    incoming = [
        {"name": "token", "value": None, "masked": True},
        {"name": "region", "value": "eu", "masked": False},
        {"name": "pin", "value": None, "mode": "hidden", "masked": True},
    ]
    assert merge_masked_parameters(STORED, incoming) == [
        {"name": "token", "value": "s3cr3t", "masked": True},
        {"name": "region", "value": "eu", "masked": False},
        {"name": "pin", "value": "1234", "mode": "hidden", "masked": True},
    ]


def test_a_typed_value_replaces_the_secret():
    incoming = [{"name": "token", "value": "rotated", "masked": True}]
    assert merge_masked_parameters(STORED, incoming) == [
        {"name": "token", "value": "rotated", "masked": True}
    ]


def test_a_row_left_out_is_removed():
    incoming = [{"name": "region", "value": "us", "masked": False}]
    assert merge_masked_parameters(STORED, incoming) == [
        {"name": "region", "value": "us", "masked": False}
    ]


def test_a_value_is_never_copied_onto_an_unmasked_row():
    # The stored token is masked, the incoming row is not: an unmasked row
    # with no value stays without one (it would otherwise print the secret).
    incoming = [{"name": "token", "value": None, "masked": False}]
    assert merge_masked_parameters(STORED, incoming) == [
        {"name": "token", "value": None, "masked": False}
    ]
    # The stored region is NOT masked: a masked incoming row with no value
    # does not inherit it either.
    incoming = [{"name": "region", "value": None, "masked": True}]
    assert merge_masked_parameters(STORED, incoming) == [
        {"name": "region", "value": None, "masked": True}
    ]


def test_a_new_masked_row_without_a_value_stays_empty():
    incoming = [{"name": "brand_new", "value": None, "masked": True}]
    assert merge_masked_parameters(STORED, incoming) == incoming
    assert merge_masked_parameters(None, incoming) == incoming


def _case_row(parameters):
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=uuid.uuid4(), title="Pay with a card",
        description=None, objective=None, preconditions=None, steps=None,
        parameters=parameters, expected_result=None, test_data=None,
        test_type="functional", priority="medium", severity="major",
        feature_area=None, suite_name=None, test_suite_id=None, tags=None,
        status="draft", version=2, author_id=None, assignee_id=None, reviewer_id=None,
        is_automated=False, automation_status="not_automated", test_fingerprint=None,
        ai_generated=False, ai_quality_score=None, ai_review_notes=None,
        estimated_duration_minutes=None, last_executed_at=None, last_execution_status=None,
        created_at=now, updated_at=now, edited_since_approval_version=None,
    )


def test_get_case_masks_the_parameters_on_the_wire(monkeypatch):
    """GET /cases/{id}: the stored secret is not in the response body."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import test_management_cases as cases
    from app.routers.test_management_shared import require_case_access

    stored = [
        {"name": "token", "value": "s3cr3t-value", "masked": True},
        {"name": "region", "value": "eu-west", "masked": False},
    ]
    case = _case_row(stored)
    monkeypatch.setattr(cases, "get_test_case_or_404", AsyncMock(return_value=case))
    monkeypatch.setattr(cases, "lifecycle_actions_for", AsyncMock(return_value=[]))
    monkeypatch.setattr(cases, "project_requires_case_review", AsyncMock(return_value=False))
    monkeypatch.setattr(cases, "_attach_latest_executions", AsyncMock())

    app = FastAPI()
    app.include_router(cases.router, prefix="/api/v1/test-management")

    async def _db():
        yield AsyncMock()

    actor = SimpleNamespace(id=uuid.uuid4(), role="VIEWER", is_active=True)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: actor
    app.dependency_overrides[require_case_access] = lambda: case

    resp = TestClient(app).get(f"/api/v1/test-management/cases/{case.id}")

    assert resp.status_code == 200, resp.text
    assert "s3cr3t-value" not in resp.text
    params = resp.json()["parameters"]
    assert params[0] == {"name": "token", "value": None, "mode": None, "masked": True}
    assert params[1]["value"] == "eu-west"
    # The row itself still holds the secret (the mask is applied to a copy).
    assert case.parameters[0]["value"] == "s3cr3t-value"
