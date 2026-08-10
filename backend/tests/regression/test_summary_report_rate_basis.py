"""The summary report's rate basis is unique tests, and it is not the headline's.

``summary_report_service`` documented ``weighted_pass_rate_pct`` as *"matches
the /overview headline so two surfaces agree"*. It does not, and cannot —
the two are computed over different populations. Measured live on Checkout
Service over 30 days:

=========================================  ==================  =======
surface                                    population          rate
=========================================  ==================  =======
``/overview`` (``avg_pass_rate_7d``)       executions          81.0%
``metrics/trends`` (weighted)              executions          81.03%
``analytics/coverage`` (``avg_pass_rate``) executions          81.0%
**this report** (``weighted_pass_rate_pct``) **unique tests**  **83.3%**
=========================================  ==================  =======

DB truth: 47 passed / 9 failed / 2 broken / 2 skipped across 60 executions of
12 unique tests. ``47/58 = 81.03``; ``10/12 = 83.3``. The BROKEN executions
disappear from this report because a unique test carries a single status, so
its broken runs are not separately counted — which is precisely why the rates
diverge.

**Both figures are internally correct.** Which one a user should see as "the"
pass rate is a product decision, recorded in the exploratory ledger. This test
does not assert that they agree — it pins **what each basis is**, so the
semantics cannot drift silently while that decision is pending, and so anyone
tempted to "make them match" sees the trade-off first: the unique-test basis is
what makes this report's counts line up with Coverage's ``unique_tests``.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.services import summary_report_service  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(summary_report_service)


def test_the_false_equality_claim_is_gone():
    """The comment asserted an equality that live measurement disproves."""
    assert "matches the\n            # /overview headline" not in SOURCE, (
        "the stale claim is back: this rate is over unique tests, the "
        "/overview headline is over executions, and they measured 83.3 vs 81.0"
    )


def test_the_basis_is_documented_where_the_value_is_built():
    """A reader at the call site must see which population this covers —
    two separate analyses of this number were nearly filed wrong because the
    comment said the opposite of the truth."""
    assert "UNIQUE TESTS" in SOURCE and "EXECUTIONS" in SOURCE


def test_weighted_rate_still_excludes_skips():
    """The one property the two surfaces genuinely share.

    ``evaluated`` is passed + failed + broken; dividing by ``total`` would
    let skipped tests drag the rate down and break the comparison with every
    other surface.
    """
    assert "totals.passed / totals.evaluated * 100.0" in SOURCE


def test_evaluated_excludes_skipped_and_includes_broken():
    """Pins the denominator itself.

    A BROKEN test that silently left the denominator is the shape of an
    earlier bug on the trend line (83.9% charted where the headline said
    81.0%), so it is worth an explicit assertion rather than trust.
    """
    src = inspect.getsource(summary_report_service)
    assert "evaluated = passed + failed + broken" in src
