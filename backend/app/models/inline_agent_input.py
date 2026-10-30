"""The closed inline-input contract of ``agent.summary.v1`` (W23c AGT-11).

``POST /api/v1/agents/{agent_id}/invoke`` accepts ``inline_input`` for the
agents in ``services.inline_input_vocab.INLINE_INPUT_AGENTS``. The payload is
shaped as the graph state the summary stage really reads (failures with name,
suite, category, confidence, root cause and a log excerpt) — the catalog's
``RunEvidenceBundleV1`` carries no free text and is not what the stage
consumes (contract §0.12).

This is NOT a registry contract: it is deliberately absent from
``agent_catalog.CATALOG_SCHEMA_MODULES`` (the catalog advertises it additively
as ``inline_input_json_schema``).

The payload is attacker-controlled: every model is closed (``extra="forbid"``),
every string is length-capped, and every string rejects NUL and the C0 control
characters except tab and newline, so nothing invisible reaches the model.
Redaction and the data-block wrapping happen later, in
``services/inline_invocation`` / ``services/untrusted_input``.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.inline_input_vocab import (
    INLINE_FAILURES_MAX,
    LOG_EXCERPT_MAX,
    ROOT_CAUSE_MAX,
    SUITE_NAME_MAX,
    TEST_NAME_MAX,
)

# NUL and every C0 control character except \t (0x09) and \n (0x0a).
_FORBIDDEN_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f]")

InlineFailureCategory = Literal[
    "PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY", "UNKNOWN",
]


def _reject_control_characters(value: Optional[str]) -> Optional[str]:
    if value is not None and _FORBIDDEN_CONTROL.search(value):
        raise ValueError("must not contain NUL or control characters other than tab and newline")
    return value


class InlineFailureV1(BaseModel):
    """One failed test as the caller describes it (its own claim, wrapped as data)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    test_name: str = Field(min_length=1, max_length=TEST_NAME_MAX)
    suite_name: Optional[str] = Field(default=None, max_length=SUITE_NAME_MAX)
    failure_category: InlineFailureCategory = "UNKNOWN"
    # REQUIRED: the caller's own claim (the summary drops < 50 from its bullets, as for any analysis).
    confidence_score: int = Field(ge=0, le=100)
    root_cause: str = Field(default="", max_length=ROOT_CAUSE_MAX)
    log_excerpt: Optional[str] = Field(default=None, max_length=LOG_EXCERPT_MAX)

    @field_validator("test_name", "suite_name", "root_cause", "log_excerpt")
    @classmethod
    def _no_control_characters(cls, value: Optional[str]) -> Optional[str]:
        return _reject_control_characters(value)


class InlineSummaryInputV1(BaseModel):
    """The inline input of ``agent.summary.v1``: 1..INLINE_FAILURES_MAX failures."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    failures: list[InlineFailureV1] = Field(min_length=1, max_length=INLINE_FAILURES_MAX)


__all__ = ["InlineFailureV1", "InlineSummaryInputV1", "InlineFailureCategory"]
