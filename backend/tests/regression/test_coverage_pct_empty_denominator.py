"""Regression guard: an ownership-coverage ratio with an empty denominator
must report "not measurable", not 0%.

The defect this guards
----------------------
``compute_coverage`` ended with::

    summary["coverage_pct"] = round((matched / located) * 100, 1) if located else 0.0

The ``else 0.0`` collapses "undefined" into "zero". Those readings lead
somewhere different: **0%** says *your rules cover none of your failures,
write more rules*; **not measurable** says *no failure here can be traced to
a file path, so no path rule can ever help*.

It is not an edge case. ``locate_failure_path`` leaves **Java deliberately
unlocated**, and TestLookup ingests TestNG and JUnit — so for an all-Java
project every failure is unlocatable and the badge showed a permanent,
unfixable 0%. The function's own docstring says non-locatable failures are
excluded because they "would otherwise drag the number down misleadingly";
when they are *all* non-locatable the old code dragged it all the way down
anyway.

Confirmed live before the fix, on a project seeded with the Java ground-truth
fixture and one ``path`` rule::

    GET /api/v1/projects/{id}/ownership/codeowners/coverage
    {"path_rules":1,"codeowners_rules":0,"sampled":3,"located":0,
     "matched":0,"coverage_pct":0.0,"lookback_days":30}

The UI badge renders exactly when ``path_rules > 0``, so that project showed
"Coverage 0%" beside a tooltip that read "0/0 ... matched a path rule".
0/0 is not 0%.

Class: §6.5 label/unit mismatch (an unmeasurable rendered as a measurement)
crossed with §6.6 (a value reported because the code could not look).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.services.codeowners_service as cs


class _ScriptedDB:
    """Returns pre-built results in order, one per ``execute``."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0)


def _scalars(rows):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def _all(rows):
    return SimpleNamespace(all=lambda: rows)


def _rule(pattern, team):
    return SimpleNamespace(
        match_type="path",
        match_pattern=pattern,
        team_name=team,
        service_name="hand",
        priority=0,
    )


@pytest.mark.regression
@pytest.mark.asyncio
async def test_all_failures_unlocatable_is_not_measurable():
    """The live repro: rules exist, failures exist, none can be located."""
    rules = [_rule("src/api/**", "@api")]
    # Java frames — locate_failure_path leaves these unlocated by design.
    sample = [
        SimpleNamespace(
            stack_trace="java.net.ConnectException: Connection refused: db.internal:5432",
            error_message=None,
        ),
        SimpleNamespace(
            stack_trace=None, error_message="AssertionError: expected 1 but was 2",
        ),
        SimpleNamespace(
            stack_trace="\tat com.auth.LoginTest.testLoginSuccess(LoginTest.java:42)",
            error_message=None,
        ),
    ]
    db = _ScriptedDB([_scalars(rules), _all(sample)])

    cov = await cs.compute_coverage(db, uuid.uuid4())

    assert cov["sampled"] == 3, "the sample must actually have been read"
    assert cov["located"] == 0
    assert cov["coverage_pct"] is None, (
        "0/0 is undefined. Reporting 0.0 tells an all-Java project its rules "
        "cover nothing, which no number of extra rules can change."
    )


@pytest.mark.regression
@pytest.mark.asyncio
async def test_zero_matches_of_locatable_failures_really_is_zero_percent():
    """The other half of the contract, and the reason None is not enough.

    When failures ARE locatable and none match, 0.0 is the correct, honest
    answer — writing more rules genuinely would help. A fix that returned
    None whenever ``matched == 0`` would destroy this signal, so the guard
    pins both directions.
    """
    rules = [_rule("src/api/**", "@api")]
    sample = [
        SimpleNamespace(stack_trace='File "src/web/y.py", line 5, in g', error_message=None),
        SimpleNamespace(stack_trace='File "src/ui/z.py", line 9, in h', error_message=None),
    ]
    db = _ScriptedDB([_scalars(rules), _all(sample)])

    cov = await cs.compute_coverage(db, uuid.uuid4())

    assert cov["located"] == 2
    assert cov["matched"] == 0
    assert cov["coverage_pct"] == 0.0


@pytest.mark.regression
@pytest.mark.asyncio
async def test_a_real_ratio_is_still_a_number():
    rules = [_rule("src/api/**", "@api")]
    sample = [
        SimpleNamespace(stack_trace='File "src/api/x.py", line 3, in f', error_message=None),
        SimpleNamespace(stack_trace='File "src/web/y.py", line 5, in g', error_message=None),
    ]
    db = _ScriptedDB([_scalars(rules), _all(sample)])

    cov = await cs.compute_coverage(db, uuid.uuid4())

    assert cov["coverage_pct"] == 50.0


@pytest.mark.regression
def test_the_response_contract_can_carry_not_measurable():
    """A service returning None is useless if the response model coerces it.

    ``coverage_pct`` was ``float = 0.0``; Pydantic would have rejected None
    and the endpoint would have 500'd instead of reporting the honest value.
    """
    from app.models.schemas import CodeownersCoverage

    model = CodeownersCoverage(
        path_rules=1, codeowners_rules=0, sampled=3, located=0,
        matched=0, coverage_pct=None, lookback_days=30,
    )
    assert model.coverage_pct is None
    assert model.model_dump()["coverage_pct"] is None
