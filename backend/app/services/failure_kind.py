"""Derived failure-kind triad — product / test_code / infrastructure / unknown.

PMF backlog US-9.1. Practitioners triage a *product regression*, a *broken
test script*, and a *flaky CI runner* completely differently, so every
analyzed failure is bucketed into one of three actionable kinds (plus
``unknown``). The kind is **derived, never stored**: it is a pure function of
two columns that already exist on every failing row —

* ``failure_category`` — the AI/rules classifier verdict (the
  :class:`app.models.postgres.FailureCategory` vocabulary, possibly with
  historical drift / LLM aliases), and
* ``status`` — the TestNG-style execution outcome shared by all parsers,
  where ``BROKEN`` means "the test errored unexpectedly" (setup blew up,
  connection died) as opposed to ``FAILED`` = "an assertion was checked and
  did not hold".

Mapping rationale (every ``FailureCategory`` member is enumerated so a new
member breaks the regression matrix in ``tests/services/test_failure_kind.py``
rather than silently landing in ``unknown``):

======================  ================  =======================================
FailureCategory         failure_kind      why
======================  ================  =======================================
``PRODUCT_BUG``         ``product``       assertion mismatch / regression in the
                                          system under test — route to a developer.
``INFRASTRUCTURE``      ``infrastructure``  environment, network, platform, OOM,
                                          timeout shapes — route to SRE / re-run
                                          after the environment recovers.
``TEST_DATA``           ``test_code``     bad fixture / seed / stale snapshot is
                                          owned by whoever owns the test suite,
                                          not the product team — same triage lane
                                          as a broken script.
``AUTOMATION_DEFECT``   ``test_code``     the test script itself is broken
                                          (locator drift, null deref in test code).
``FLAKY``               ``test_code``     non-determinism is overwhelmingly a
                                          test-side race/timing/fixture problem;
                                          quarantine + stabilise is test-suite
                                          work. (When the flake is actually infra
                                          noise the classifier emits
                                          ``INFRASTRUCTURE`` instead.)
``UNKNOWN``             ``unknown``       classifier could not decide — subject
                                          to the BROKEN nudge below.
======================  ================  =======================================

BROKEN nudge: when the category is absent / UNKNOWN / unrecognisable, a
``BROKEN`` status is the strongest remaining signal — every parser reserves
it for "unexpected error" shapes (exception outside an assertion), which in
practice skews heavily toward environment problems. Those rows therefore
resolve to ``infrastructure`` instead of ``unknown``. A real category always
wins over the nudge.

Alias handling reuses :mod:`app.services.category_normalizer`'s deterministic
alias map (e.g. LLM outputs like ``ENV``/``TEST_CODE``/``INTERMITTENT``), so
this module never re-invents normalisation. Unrecognised strings fall back to
``unknown`` (then the BROKEN nudge may still apply).

Copy discipline (US-9.2): kinds are AI-derived classifications, presented in
the UI with the existing provenance affordances — never as ground truth.
The frontend mirror of this mapping lives in
``frontend/src/utils/failureKind.ts``; keep the two in sync.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from app.models.postgres import FailureCategory
from app.services.category_normalizer import CATEGORY_ALIASES

# Canonical kind values, in the fixed presentation order the UI uses.
KIND_PRODUCT = "product"
KIND_TEST_CODE = "test_code"
KIND_INFRASTRUCTURE = "infrastructure"
KIND_UNKNOWN = "unknown"

FAILURE_KINDS: tuple[str, ...] = (
    KIND_PRODUCT,
    KIND_TEST_CODE,
    KIND_INFRASTRUCTURE,
    KIND_UNKNOWN,
)

# Exhaustive category → kind map. Every FailureCategory member MUST appear
# here — tests/services/test_failure_kind.py enforces exhaustiveness so a
# new enum member fails loudly instead of silently mapping to unknown.
KIND_BY_CATEGORY: dict[str, str] = {
    FailureCategory.PRODUCT_BUG.value: KIND_PRODUCT,
    FailureCategory.INFRASTRUCTURE.value: KIND_INFRASTRUCTURE,
    FailureCategory.TEST_DATA.value: KIND_TEST_CODE,
    FailureCategory.AUTOMATION_DEFECT.value: KIND_TEST_CODE,
    FailureCategory.FLAKY.value: KIND_TEST_CODE,
    FailureCategory.UNKNOWN.value: KIND_UNKNOWN,
}


def failure_kind(failure_category: str | None, status: str | None) -> str:
    """Return the derived failure kind for one analyzed failure.

    Pure and side-effect free (no logging, no DB) so it is safe inside
    aggregation loops and Pydantic computed fields.

    Args:
        failure_category: Stored classifier verdict (``FailureCategory``
            value, a known alias, historical lowercase drift, or None).
        status: Execution status string (``FAILED`` / ``BROKEN`` / ...) or
            None when the caller only has category-level aggregates. Without
            a status the BROKEN nudge cannot apply — uncategorised rows stay
            ``unknown`` (fidelity limit for aggregate callers).

    Returns:
        One of ``FAILURE_KINDS``.
    """
    raw = str(getattr(failure_category, "value", failure_category) or "").strip().upper()
    kind = KIND_BY_CATEGORY.get(raw)
    if kind is None and raw:
        alias = CATEGORY_ALIASES.get(raw)
        if alias:
            kind = KIND_BY_CATEGORY.get(alias)
    if kind is None or kind == KIND_UNKNOWN:
        # BROKEN nudge — unexpected-error shape with no usable category.
        if str(status or "").strip().upper() == "BROKEN":
            return KIND_INFRASTRUCTURE
        return KIND_UNKNOWN
    return kind


def kind_counts(rows: Iterable[tuple[str | None, str | None, int]]) -> list[dict]:
    """Aggregate ``(failure_category, status, count)`` rows into by-kind items.

    Returns one item per kind in the fixed ``FAILURE_KINDS`` order — zero
    counts included so UI chips stay stable across windows.
    """
    counter: Counter[str] = Counter()
    for category, status, count in rows:
        counter[failure_kind(category, status)] += int(count or 0)
    return [{"kind": kind, "count": counter.get(kind, 0)} for kind in FAILURE_KINDS]
