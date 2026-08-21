"""Regression guard: a computed field must survive its response_model.

The defect (F-067, half-shipped)
--------------------------------
Two surfaces report different pass rates over the same window — /overview
81.0% (every execution) and the Summary Report 83.3% (each distinct test
once). Both are correct. #588 resolved the ambiguity by *publishing the
basis* on both, and ``summary_report_service`` duly set::

    "pass_rate_basis": PASS_RATE_BASIS_UNIQUE_TESTS,
    "pass_rate_basis_label": PASS_RATE_BASIS_LABELS[...],

``SummaryTotals`` never declared those fields. **A Pydantic ``response_model``
drops undeclared keys**, so the service computed the basis and the API threw
it away — measured live: /overview returned
``basis_label: "per test execution"`` while the report returned ``83.3`` bare.
Half the fix shipped, and the surface that most needed the label was the one
that lost it.

The general trap
----------------
A service can set any key it likes; only what the schema declares reaches the
client. That failure is silent in both directions — no error, no warning, and
the service code reads as though it works. This has bitten the codebase before
from the other side: #492 added ``total_executions`` to one branch of
``_period_stats`` and not the other, which raised KeyError -> HTTP 500 on
every suite-filtered dashboard request.

So this guard checks the *contract*, not one field name: every basis key the
service writes must be declared on the model that serialises it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.regression

BACKEND = pathlib.Path(__file__).resolve().parents[2]
SERVICE = BACKEND / "app" / "services" / "summary_report_service.py"
SCHEMAS = BACKEND / "app" / "models" / "schemas.py"


def _keys_the_service_sets() -> set[str]:
    """Dict keys mentioning ``basis`` that the service writes into totals."""
    src = SERVICE.read_text(encoding="utf-8")
    # [A-Za-z0-9_], not [a-z_]: a mutation that renamed the key to
    # ``pass_rate_basis_labelX`` slipped past the narrower class entirely, so
    # the guard "passed" without ever seeing the renamed key. A guard whose
    # matcher cannot see the change is not guarding.
    return set(re.findall(r'"([A-Za-z0-9_]*basis[A-Za-z0-9_]*)"\s*:', src))


def test_the_guard_found_the_service_keys():
    """Fail-open: nothing parsed means the comparison below proves nothing."""
    keys = _keys_the_service_sets()
    assert keys, (
        "no basis keys found in summary_report_service. Either they were "
        "renamed or removed; this guard would otherwise pass vacuously."
    )


def test_every_basis_key_the_service_sets_is_declared_on_the_schema():
    from app.models.schemas import SummaryTotals

    declared = set(SummaryTotals.model_fields)
    dropped = sorted(_keys_the_service_sets() - declared)
    assert not dropped, (
        f"summary_report_service sets {dropped}, but SummaryTotals does not "
        "declare them — a Pydantic response_model DROPS undeclared keys, so "
        "the service computes these and the API silently discards them. "
        f"Declared: {sorted(declared)}"
    )


def test_the_report_declares_a_basis_at_all():
    """The whole point of F-067: the number must carry its population."""
    from app.models.schemas import SummaryTotals

    fields = SummaryTotals.model_fields
    assert "pass_rate_basis" in fields and "pass_rate_basis_label" in fields, (
        "SummaryTotals publishes a pass rate with no basis. It reports each "
        "distinct test once (83.3% on the measured window) while /overview "
        "reports every execution (81.0%); without the basis those read as a "
        "contradiction rather than two answers to two questions."
    )


def test_the_two_bases_are_distinct_and_labelled():
    """Fail-open: identical labels would make publishing them pointless."""
    from app.services.metrics_service import (
        PASS_RATE_BASIS_EXECUTIONS,
        PASS_RATE_BASIS_LABELS,
        PASS_RATE_BASIS_UNIQUE_TESTS,
    )

    assert PASS_RATE_BASIS_EXECUTIONS != PASS_RATE_BASIS_UNIQUE_TESTS
    labels = {
        PASS_RATE_BASIS_LABELS[PASS_RATE_BASIS_EXECUTIONS],
        PASS_RATE_BASIS_LABELS[PASS_RATE_BASIS_UNIQUE_TESTS],
    }
    assert len(labels) == 2, f"both bases render the same label: {labels}"


def test_the_dashboard_basis_is_declared_too():
    """The other half of the pair — /overview's basis reaches its consumer.

    That side already worked; pin it so the fix cannot regress asymmetrically
    the way it shipped asymmetrically.
    """
    src = (BACKEND / "app" / "services" / "metrics_service.py").read_text(encoding="utf-8")
    assert '"basis"' in src and '"basis_label"' in src, (
        "metrics_service no longer publishes the dashboard's pass-rate basis"
    )
