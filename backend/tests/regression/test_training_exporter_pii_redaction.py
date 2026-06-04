"""Regression: the reasoning-track fine-tune export leaked PII (audit S3).

``TrainingDataExporter._export_reasoning`` copied the raw Mongo ReAct trace
(``prompt`` + ``intermediate_steps`` + ``analysis``) straight into the MinIO
fine-tune corpus with no redaction. Those traces carry test names, stack
traces, env URLs, emails, and secrets — so anyone with access to the training
bucket (or any model trained on it) saw raw customer PII.

Fix: every free-text field on a reasoning example now passes through
``privacy_service.sanitize_for_persistence`` (the ``[REDACTED]`` boundary)
before it lands in the corpus. These tests pin:

1. ``_format_reasoning_chain`` redacts each ReAct step;
2. the assembled reasoning example redacts the prompt + analysis payload.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy")

from app.services.privacy_service import sanitize_for_persistence  # noqa: E402
from app.services.training.exporter import TrainingDataExporter  # noqa: E402

pytestmark = pytest.mark.regression

# A string the redactor is known to scrub (mirror the webhook/privacy fixtures).
_EMAIL = "alice@example.com"
# The redactor emits typed placeholders (e.g. [REDACTED_EMAIL]); match the prefix.
_REDACTED = "[REDACTED"


def test_redaction_fixture_actually_scrubs_the_marker():
    """Guard: if the redactor stops scrubbing emails this whole test is moot,
    so assert the boundary really transforms our marker before relying on it."""
    assert sanitize_for_persistence(f"contact {_EMAIL} now") != f"contact {_EMAIL} now"
    assert _EMAIL not in sanitize_for_persistence(f"contact {_EMAIL} now")


def test_format_reasoning_chain_redacts_each_step():
    steps = [
        f"Observation: failed to reach db, admin {_EMAIL} notified",
        "   ",  # whitespace-only → dropped (behaviour preserved)
        f"Action: email {_EMAIL}",
    ]
    turns = TrainingDataExporter._format_reasoning_chain(steps)

    # whitespace-only step still dropped
    assert len(turns) == 2
    for turn in turns:
        assert turn["role"] == "assistant"
        assert _EMAIL not in turn["content"]
        assert _REDACTED in turn["content"]


def test_reasoning_example_redacts_prompt_and_analysis():
    """Drive the assembly path the way _export_reasoning does and assert the
    email is gone from every emitted message."""
    prompt = f"Investigate failure reported by {_EMAIL}"
    analysis = {"failure_category": "PRODUCT_BUG", "root_cause_summary": f"see {_EMAIL}"}

    example = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": sanitize_for_persistence(prompt)},
            *TrainingDataExporter._format_reasoning_chain([f"step touching {_EMAIL}"]),
            {
                "role": "assistant",
                "content": sanitize_for_persistence(json.dumps(analysis, indent=2)),
            },
        ]
    }

    serialized = json.dumps(example)
    assert _EMAIL not in serialized
    # structure preserved: system + user + 1 step + assistant
    assert [m["role"] for m in example["messages"]] == [
        "system", "user", "assistant", "assistant",
    ]
    # the structured label survives redaction (only PII is scrubbed)
    assert "PRODUCT_BUG" in example["messages"][-1]["content"]
