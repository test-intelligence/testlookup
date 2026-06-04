"""Regression: unbounded string fields on persisted Pydantic models (audit S4).

Several `*Create`/`*Update` request models accepted arbitrarily long strings
for fields persisted to the DB. A QA_ENGINEER+ could POST a multi-MB/GB string
(test-case body, plan/strategy free-text, description) and exhaust memory / DB
write capacity (OWASP A03, resource exhaustion). `UserCreate.password` was the
sharpest case — an unbounded password is hashed on the bcrypt path, so a huge
value is a cheap CPU/memory DoS.

Fix: cap each field with `Field(max_length=...)`. Long-form `Text`-backed
fields use the shared `MAX_LONG_TEXT` (generous — only rejects pathological
payloads, so realistic content is untouched); `String(N)`-backed fields match
`N` exactly (so an over-long value returns a clean 422 instead of a DB 500).

These tests pin: over-limit input raises ValidationError; at-limit and normal
input still validate (behaviour-preserving).
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("pydantic")

from pydantic import ValidationError  # noqa: E402

from app.models.schemas import (  # noqa: E402
    MAX_LONG_TEXT,
    ChatSessionCreate,
    KnowledgeSourceUpdate,
    ManagedTestCaseCreate,
    QualityGateCreate,
    TestStrategyUpdate,
    UserCreate,
)

pytestmark = pytest.mark.regression


def test_long_text_constant_is_generous_but_bounded():
    # Big enough not to reject realistic content, small enough to block multi-MB abuse.
    assert 10_000 <= MAX_LONG_TEXT <= 1_000_000


# ── Tier A: long-form Text fields ────────────────────────────────────────────

def test_managed_test_case_description_over_limit_rejected():
    with pytest.raises(ValidationError):
        ManagedTestCaseCreate(
            project_id=uuid.uuid4(),
            title="valid title",
            description="x" * (MAX_LONG_TEXT + 1),
        )


def test_managed_test_case_at_limit_accepted():
    # At exactly the cap (and a normal-size value) still validates.
    ok = ManagedTestCaseCreate(
        project_id=uuid.uuid4(),
        title="valid title",
        description="x" * MAX_LONG_TEXT,
        test_data="small fixture",
    )
    assert len(ok.description) == MAX_LONG_TEXT
    assert ok.test_data == "small fixture"


# ── Tier B: String(N) fields match the column width ──────────────────────────

@pytest.mark.parametrize(
    "factory, kwargs, overlong_field, cap",
    [
        (ChatSessionCreate, {}, "title", 500),
        (QualityGateCreate, {"rules": []}, "name", 255),
        (TestStrategyUpdate, {}, "version_label", 50),
        (KnowledgeSourceUpdate, {}, "classification", 20),
    ],
)
def test_string_column_fields_reject_over_width(factory, kwargs, overlong_field, cap):
    # cap+1 → rejected
    with pytest.raises(ValidationError):
        factory(**{**kwargs, overlong_field: "x" * (cap + 1)})
    # exactly cap → accepted
    obj = factory(**{**kwargs, overlong_field: "x" * cap})
    assert len(getattr(obj, overlong_field)) == cap


# ── Tier C: password DoS guard ───────────────────────────────────────────────

def test_user_create_password_over_128_rejected():
    with pytest.raises(ValidationError):
        UserCreate(
            email="a@example.com",
            username="alice",
            password="x" * 129,
        )


def test_user_create_password_within_bounds_accepted():
    ok = UserCreate(
        email="a@example.com",
        username="alice",
        password="x" * 128,
        full_name="Alice Example",
    )
    assert ok.full_name == "Alice Example"
