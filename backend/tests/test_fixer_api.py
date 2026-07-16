"""Fixer router request-model validation + gate-error mapping (AI-2).

DB-free: constructs the PUT body models and checks the policy layer rejects
``act`` and invalid runner/schedule values, and that the gate error types the
router maps to 403/409/422 exist and are distinct.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

from pydantic import ValidationError  # noqa: E402

from app.routers.fixer import (  # noqa: E402
    FixerBudgets,
    FixerConfigUpdate,
    FixerRunner,
)
from app.services import fixer_service as svc  # noqa: E402


def test_put_body_defaults_match_contract():
    body = FixerConfigUpdate()
    assert body.enabled is False and body.mode == "shadow"
    assert body.runner.type == "none"
    assert body.test_globs == ["tests/**", "**/*.spec.*", "**/*.test.*"]
    assert body.budgets.model_dump() == {
        "max_tests_per_run": 3, "max_attempts_per_test": 2,
        "validation_reruns": 5, "max_concurrent_open_prs": 2,
    }
    assert body.schedule == "off"


def test_put_body_rejects_act_mode():
    with pytest.raises(ValidationError):
        FixerConfigUpdate(mode="act")


def test_put_body_rejects_unknown_runner_type():
    with pytest.raises(ValidationError):
        FixerRunner(type="kubernetes")


def test_put_body_rejects_unknown_schedule():
    with pytest.raises(ValidationError):
        FixerConfigUpdate(schedule="hourly")


def test_validation_reruns_must_be_at_least_one():
    with pytest.raises(ValidationError):
        FixerBudgets(validation_reruns=0)


def test_suggest_mode_accepted():
    body = FixerConfigUpdate(mode="suggest", runner=FixerRunner(type="docker"))
    assert body.mode == "suggest" and body.runner.type == "docker"


def test_gate_error_types_are_distinct():
    assert issubclass(svc.FixerDisabled, Exception)
    assert issubclass(svc.FixerAlreadyRunning, Exception)
    assert issubclass(svc.FixerRunnerRequiredForSuggest, Exception)
    assert svc.FixerDisabled is not svc.FixerAlreadyRunning
