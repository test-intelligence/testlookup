"""A run's AI verdict, computed the way the Runs table computes it (FAIL-04 / FAIL-05).

The Runs table's AI-verdict pill is ``aiVerdictOf`` (``frontend/src/pages/runsList/
aiVerdict.ts``) over a run-intelligence payload, with ``deriveDecisionTrustState``
(``components/ai/decisionTrustState.ts``) choosing whether the decision report is
displayable. Two server paths must agree with it exactly:

* ``GET /runs/ai-verdicts`` returns, per run, a SLIM payload of the same shape
  (:func:`verdict_inputs_of`): only the values the reducer reads, each object
  keeping its JavaScript truthiness and each read leaf its JSON value. The
  browser runs the SAME reducer on it, so the column and the run's Analysis tab
  cannot disagree.
* ``test_runs.ai_recommendation`` (the ``/runs?recommendation=`` filter) is
  :func:`ai_recommendation_of` of the payload ``save_snapshot`` stores.

Both read JSON-decoded values with JavaScript's truthiness (:func:`js_truthy`):
an EMPTY object or array is truthy, ``0`` and ``""`` are not. One case table,
duplicated as literals in ``tests/test_run_verdict.py`` and
``frontend/src/pages/runsList/aiVerdict.test.ts``, pins the parity.

Pure: no database, no I/O. Vocabularies live here and only here.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

__all__ = [
    "MAX_SIGNATURE_RUN_IDS",
    "MAX_VERDICT_RUN_IDS",
    "RECOMMENDATIONS",
    "RECOMMENDATION_FILTER_VALUES",
    "RECOMMENDATION_INVALID_DETAIL",
    "RECOMMENDATION_NONE",
    "RUN_IDS_INVALID_DETAIL",
    "SIGNATURE_ROWS_PER_RUN",
    "ai_recommendation_of",
    "js_truthy",
    "verdict_inputs_of",
]

RECOMMENDATIONS: Final = ("GO", "CONDITIONAL_GO", "NO_GO")
#: ``GET /runs?recommendation=none``: the runs with no AI verdict.
RECOMMENDATION_NONE: Final = "none"
RECOMMENDATION_FILTER_VALUES: Final = (*RECOMMENDATIONS, RECOMMENDATION_NONE)
RECOMMENDATION_INVALID_DETAIL: Final = "recommendation must be one of GO, CONDITIONAL_GO, NO_GO, none."
#: ``GET /runs/ai-verdicts``: the Runs table's page size (``RunsPage.tsx``).
MAX_VERDICT_RUN_IDS: Final = 25
#: ``GET /runs/failure-signatures``: the visible failed rows first, then the newest others.
MAX_SIGNATURE_RUN_IDS: Final = 50
#: Failing rows read per run by the signature batch (its cost bound).
SIGNATURE_ROWS_PER_RUN: Final = 500
#: ``.format(max=...)``.
RUN_IDS_INVALID_DETAIL: Final = "run_id: between 1 and {max} run ids."


def js_truthy(value: Any) -> bool:
    """JavaScript's ``Boolean(x)`` over a JSON-decoded value.

    ``None`` / ``False`` -> False; ``True`` -> True; a number -> ``!= 0`` (bool
    is checked first: it is an ``int`` in Python); a string -> ``!= ""``; a dict
    or a list -> True, EVEN EMPTY (``Boolean({})`` and ``Boolean([])`` are true).
    """
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (dict, list, tuple)):
        return True
    return bool(value)


def _dig(value: Any, *keys: str) -> Any:
    """Walk dicts only; any non-dict step (a string, an array, a number) is ``None``.

    JavaScript's ``x?.a?.b`` on JSON: a property of a string or an array that is
    not one of their own (``length``) is ``undefined``.
    """
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _slim_object(value: Any, leaves: tuple[tuple[str, ...], ...]) -> Any:
    """The §1.2 rule for one OBJECT field.

    A JSON object -> an object of its read leaves (a missing one ``None``; a leaf
    keeps its JSON value and type). A non-object that JavaScript reads as truthy
    (a non-empty string, a non-zero number, ``true``, ANY array) -> ``[]``: truthy,
    and every property read on it is ``undefined``. Anything falsy -> ``None``.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for path in leaves:
            if len(path) == 1:
                out[path[0]] = value.get(path[0])
            else:
                head, leaf = path
                out.setdefault(head, {})[leaf] = _dig(value, head, leaf)
        return out
    return [] if js_truthy(value) else None


_DI_LEAVES: Final = (("verification", "status"), ("release_decision", "recommendation"))
_VER_LEAVES: Final = (("status",),)
_ATT_LEAVES: Final = (("status",), ("verification_status",))
_RD_LEAVES: Final = (("recommendation",),)


def _top_analyses_slim(value: Any) -> list[bool] | None:
    """``(top_analyses?.length ?? 0) > 0`` kept: ``[True]`` / ``[]`` / ``None``.

    A list or a string: ``[True]`` when its length is above 0, else ``[]``. A
    dict carrying a numeric ``length`` above 0 (JavaScript reads that property):
    ``[True]``. Anything else reads as no length: ``None``.
    """
    if isinstance(value, (list, str)):
        return [True] if len(value) > 0 else []
    if isinstance(value, dict):
        length = value.get("length")
        if isinstance(length, (int, float)) and not isinstance(length, bool) and length > 0:
            return [True]
    return None


def verdict_inputs_of(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The slim ``verdict_inputs`` of a FULL run-intelligence payload (§1.2).

    Same keys as the payload, only what ``aiVerdictOf`` reads. The batch route
    builds the same dict in SQL; the Postgres test asserts the two are equal.
    """
    p = payload if isinstance(payload, Mapping) else {}
    ss = p.get("structured_summary")
    summary: dict[str, Any] | None
    if isinstance(ss, dict):
        summary = {
            "decision_intelligence": _slim_object(ss.get("decision_intelligence"), _DI_LEAVES),
            "decision_report_verification": _slim_object(ss.get("decision_report_verification"), _VER_LEAVES),
            "latest_decision_attempt": _slim_object(ss.get("latest_decision_attempt"), _ATT_LEAVES),
        }
    else:
        summary = None
    return {
        "structured_summary": summary,
        "release_decision": _slim_object(p.get("release_decision"), _RD_LEAVES),
        "intelligence_available": js_truthy(p.get("intelligence_available")),
        "top_analyses": _top_analyses_slim(p.get("top_analyses")),
    }


def _recommendation(value: Any) -> str | None:
    return value if isinstance(value, str) and value in RECOMMENDATIONS else None


def ai_recommendation_of(payload: Mapping[str, Any] | None) -> str | None:
    """``aiVerdictOf(payload).recommendation`` — for a FULL or a slim payload.

    The precedence of ``aiVerdict.ts`` with ``deriveDecisionTrustState``: a
    displayable decision report's recommendation wins (verified, and the latest
    attempt and verification agree on publishing or on rejecting it); when any
    report envelope exists but none of it is displayable there is NO verdict and
    no fallback; otherwise the persisted release decision.
    """
    if not isinstance(payload, Mapping):
        return None
    ss = payload.get("structured_summary")
    if not isinstance(ss, dict):
        ss = {}
    di = ss.get("decision_intelligence")
    ver = ss.get("decision_report_verification")
    att = ss.get("latest_decision_attempt")
    # String compares: only a JSON string equals "passed" (never 1, never ["passed"]).
    report_verified = _dig(di, "verification", "status") == "passed"
    published_agrees = (
        _dig(att, "status") == "published"
        and _dig(att, "verification_status") == "passed"
        and _dig(ver, "status") == "passed"
    )
    rejection_agrees = (
        _dig(att, "status") == "rejected"
        and _dig(att, "verification_status") == "failed"
        and _dig(ver, "status") == "failed"
    )
    if report_verified and (published_agrees or rejection_agrees):
        from_report = _recommendation(_dig(di, "release_decision", "recommendation"))
        if from_report:
            return from_report
    if js_truthy(di) or js_truthy(ver) or js_truthy(att):
        return None
    return _recommendation(_dig(payload, "release_decision", "recommendation"))
