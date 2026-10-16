"""The run verdict, computed exactly as the Runs table computes it (W07 FAIL-04 / FAIL-05).

``CASES`` is the parity table of contract §1.2: the SAME 16 rows, in the SAME
order, are a literal in ``frontend/src/pages/runsList/aiVerdict.test.ts``
(``VERDICT_CASES``), where ``aiVerdictOf(full)`` and ``aiVerdictOf(slim)`` must
both equal ``verdict``. Here:

* ``verdict_inputs_of(full) == slim`` (the slim payload the batch route returns);
* ``ai_recommendation_of(full) == ai_recommendation_of(slim) == recommendation``
  (the ``test_runs.ai_recommendation`` projection).

``verdict`` is the expected ``AiVerdict``: ``{"recommendation", "review"}`` when
there is a recommendation (no ``review`` in these payloads: ``review`` is
``None``), else ``{"recommendation": None, "reason"}``. A change to either
reducer that is not made to both breaks a row on one side.
"""
from __future__ import annotations

import copy

import pytest

from app.services.run_verdict import (
    MAX_SIGNATURE_RUN_IDS,
    MAX_VERDICT_RUN_IDS,
    RECOMMENDATION_FILTER_VALUES,
    RECOMMENDATION_INVALID_DETAIL,
    RECOMMENDATIONS,
    RUN_IDS_INVALID_DETAIL,
    SIGNATURE_ROWS_PER_RUN,
    ai_recommendation_of,
    js_truthy,
    verdict_inputs_of,
)

# A verified, published decision report (the envelope that is displayable).
_PUBLISHED_VER = {"status": "passed", "failures": [], "checked_at": "2026-10-01T00:00:00Z"}
_PUBLISHED_ATT = {"status": "published", "verification_status": "passed", "attempt": 2}
_SLIM_PUBLISHED_VER = {"status": "passed"}
_SLIM_PUBLISHED_ATT = {"status": "published", "verification_status": "passed"}


def _verdict(recommendation):
    return {"recommendation": recommendation, "review": None}


def _none(reason):
    return {"recommendation": None, "reason": reason}


CASES: list[dict] = [
    {   # 1
        "name": "verified published report beats a NO_GO release decision",
        "full": {
            "intelligence_available": True,
            "release_decision": {"recommendation": "NO_GO", "confidence": 0.7},
            "structured_summary": {
                "decision_intelligence": {
                    "status": "complete",
                    "verification": {"status": "passed", "checks": 4},
                    "release_decision": {"recommendation": "GO", "rationale": "All gates green."},
                },
                "decision_report_verification": _PUBLISHED_VER,
                "latest_decision_attempt": _PUBLISHED_ATT,
                "executive": {"headline": "Ready"},
            },
            "top_analyses": [{"test_name": "test_login"}],
            "run": {"id": "r1", "failed_tests": 3},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "passed"},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": _SLIM_PUBLISHED_VER,
                "latest_decision_attempt": _SLIM_PUBLISHED_ATT,
            },
            "release_decision": {"recommendation": "NO_GO"},
            "intelligence_available": True,
            "top_analyses": [True],
        },
        "verdict": _verdict("GO"),
        "recommendation": "GO",
    },
    {   # 2
        "name": "verified report whose publication was rejected is still shown",
        "full": {
            "release_decision": {"recommendation": "GO"},
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "passed"},
                    "release_decision": {"recommendation": "CONDITIONAL_GO"},
                },
                "decision_report_verification": {"status": "failed", "failures": ["stale"]},
                "latest_decision_attempt": {"status": "rejected", "verification_status": "failed"},
            },
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "passed"},
                    "release_decision": {"recommendation": "CONDITIONAL_GO"},
                },
                "decision_report_verification": {"status": "failed"},
                "latest_decision_attempt": {"status": "rejected", "verification_status": "failed"},
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _verdict("CONDITIONAL_GO"),
        "recommendation": "CONDITIONAL_GO",
    },
    {   # 3
        "name": "a pending envelope is not displayable and never falls back",
        "full": {
            "intelligence_available": True,
            "release_decision": {"recommendation": "GO"},
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "pending"},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": {"status": "pending"},
            },
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "pending"},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": {"status": "pending"},
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": True,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
    {   # 4
        "name": "no structured summary: the release decision answers",
        "full": {
            "intelligence_available": True,
            "structured_summary": None,
            "release_decision": {"recommendation": "NO_GO", "reasons": ["3 blockers"]},
            "top_analyses": [],
        },
        "slim": {
            "structured_summary": None,
            "release_decision": {"recommendation": "NO_GO"},
            "intelligence_available": True,
            "top_analyses": [],
        },
        "verdict": _verdict("NO_GO"),
        "recommendation": "NO_GO",
    },
    {   # 5
        "name": "a release decision outside the vocabulary is no verdict",
        "full": {
            "structured_summary": {"executive": {}},
            "release_decision": {"recommendation": "MAYBE"},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": None,
                "decision_report_verification": None,
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "MAYBE"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _none("no-recommendation"),
        "recommendation": None,
    },
    {   # 6
        "name": "an EMPTY report object is truthy: an envelope, no fallback",
        "full": {
            "structured_summary": {"decision_intelligence": {}},
            "release_decision": {"recommendation": "GO"},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": None},
                    "release_decision": {"recommendation": None},
                },
                "decision_report_verification": None,
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
    {   # 7
        "name": "an empty-string report is falsy: the release decision answers",
        "full": {
            "structured_summary": {"decision_intelligence": ""},
            "release_decision": {"recommendation": "GO"},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": None,
                "decision_report_verification": None,
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _verdict("GO"),
        "recommendation": "GO",
    },
    {   # 8
        "name": "a non-empty string report is truthy: [] in the slim",
        "full": {
            "structured_summary": {"decision_intelligence": "x"},
            "release_decision": {"recommendation": "GO"},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": [],
                "decision_report_verification": None,
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
    {   # 9
        "name": "a zero report is falsy: the release decision answers",
        "full": {
            "structured_summary": {"decision_intelligence": 0},
            "release_decision": {"recommendation": "CONDITIONAL_GO"},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": None,
                "decision_report_verification": None,
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "CONDITIONAL_GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _verdict("CONDITIONAL_GO"),
        "recommendation": "CONDITIONAL_GO",
    },
    {   # 10
        "name": "a report of 1 is truthy: an envelope, no fallback",
        "full": {
            "structured_summary": {"decision_intelligence": 1},
            "release_decision": {"recommendation": "NO_GO"},
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": [],
                "decision_report_verification": None,
                "latest_decision_attempt": None,
            },
            "release_decision": {"recommendation": "NO_GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
    {   # 11
        "name": "a verification status of the number 1 is never passed",
        "full": {
            "release_decision": {"recommendation": "GO"},
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": 1},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": _PUBLISHED_VER,
                "latest_decision_attempt": _PUBLISHED_ATT,
            },
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": 1},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": _SLIM_PUBLISHED_VER,
                "latest_decision_attempt": _SLIM_PUBLISHED_ATT,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
    {   # 12
        "name": "a verification status of [\"passed\"] is never passed",
        "full": {
            "release_decision": {"recommendation": "GO"},
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": ["passed"]},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": _PUBLISHED_VER,
                "latest_decision_attempt": _PUBLISHED_ATT,
            },
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": ["passed"]},
                    "release_decision": {"recommendation": "GO"},
                },
                "decision_report_verification": _SLIM_PUBLISHED_VER,
                "latest_decision_attempt": _SLIM_PUBLISHED_ATT,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": False,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
    {   # 13
        "name": "no analyses, intelligence_available 0: not analysed",
        "full": {"intelligence_available": 0, "top_analyses": [], "run": {"id": "r13"}},
        "slim": {
            "structured_summary": None,
            "release_decision": None,
            "intelligence_available": False,
            "top_analyses": [],
        },
        "verdict": _none("not-analysed"),
        "recommendation": None,
    },
    {   # 14
        "name": "a list of analyses: analysed, no recommendation",
        "full": {"top_analyses": [{"test_name": "test_a", "failure_category": "PRODUCT_BUG"}]},
        "slim": {
            "structured_summary": None,
            "release_decision": None,
            "intelligence_available": False,
            "top_analyses": [True],
        },
        "verdict": _none("no-recommendation"),
        "recommendation": None,
    },
    {   # 15
        "name": "a string of analyses has a length too: analysed",
        "full": {"top_analyses": "abc", "release_decision": None},
        "slim": {
            "structured_summary": None,
            "release_decision": None,
            "intelligence_available": False,
            "top_analyses": [True],
        },
        "verdict": _none("no-recommendation"),
        "recommendation": None,
    },
    {   # 16
        "name": "a verified report with a null recommendation is no verdict, no fallback",
        "full": {
            "intelligence_available": True,
            "release_decision": {"recommendation": "GO"},
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "passed"},
                    "release_decision": {"recommendation": None},
                },
                "decision_report_verification": _PUBLISHED_VER,
                "latest_decision_attempt": _PUBLISHED_ATT,
            },
        },
        "slim": {
            "structured_summary": {
                "decision_intelligence": {
                    "verification": {"status": "passed"},
                    "release_decision": {"recommendation": None},
                },
                "decision_report_verification": _SLIM_PUBLISHED_VER,
                "latest_decision_attempt": _SLIM_PUBLISHED_ATT,
            },
            "release_decision": {"recommendation": "GO"},
            "intelligence_available": True,
            "top_analyses": None,
        },
        "verdict": _none("not-verified"),
        "recommendation": None,
    },
]


def test_the_table_has_the_contracts_sixteen_rows():
    # The frontend copy asserts the same count; a row added on one side only fails here or there.
    assert len(CASES) == 16
    assert len({case["name"] for case in CASES}) == 16


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_slim_is_built_from_the_full_payload(case):
    assert verdict_inputs_of(case["full"]) == case["slim"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_full_and_slim_project_the_same_recommendation(case):
    assert ai_recommendation_of(case["full"]) == case["recommendation"]
    assert ai_recommendation_of(case["slim"]) == case["recommendation"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_expected_verdict_agrees_with_the_recommendation(case):
    # The table is self-consistent: the pill the frontend expects is the column's value.
    assert case["verdict"]["recommendation"] == case["recommendation"]
    if case["recommendation"] is None:
        assert case["verdict"]["reason"] in {"not-analysed", "no-recommendation", "not-verified"}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_slimming_never_mutates_the_payload(case):
    full = copy.deepcopy(case["full"])
    verdict_inputs_of(full)
    ai_recommendation_of(full)
    assert full == case["full"]


def test_the_slim_of_the_slim_is_itself():
    # The slim is a fixed point: the batch route's output read again gives the same verdict inputs.
    for case in CASES:
        assert verdict_inputs_of(case["slim"]) == case["slim"]


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, False), (False, False), (True, True),
        (0, False), (0.0, False), (1, True), (-2.5, True),
        ("", False), ("x", True), ("0", True),
        ({}, True), ([], True), ({"a": 1}, True), ([0], True),
    ],
)
def test_js_truthy_is_javascripts_boolean(value, expected):
    assert js_truthy(value) is expected


def test_a_payload_that_is_not_an_object_has_no_verdict():
    for payload in (None, [], "GO", 3):
        assert ai_recommendation_of(payload) is None
        assert verdict_inputs_of(payload) == {
            "structured_summary": None,
            "release_decision": None,
            "intelligence_available": False,
            "top_analyses": None,
        }


def test_top_analyses_reads_a_numeric_length_only():
    assert verdict_inputs_of({"top_analyses": {"length": 2}})["top_analyses"] == [True]
    assert verdict_inputs_of({"top_analyses": {"length": 0}})["top_analyses"] is None
    assert verdict_inputs_of({"top_analyses": {"length": True}})["top_analyses"] is None
    assert verdict_inputs_of({"top_analyses": {"length": "3"}})["top_analyses"] is None
    assert verdict_inputs_of({"top_analyses": 5})["top_analyses"] is None


def test_a_non_object_verification_reads_as_a_null_status():
    slim = verdict_inputs_of({"structured_summary": {"decision_intelligence": {"verification": "passed"}}})
    assert slim["structured_summary"]["decision_intelligence"]["verification"] == {"status": None}


def test_the_vocabularies_and_limits():
    assert RECOMMENDATIONS == ("GO", "CONDITIONAL_GO", "NO_GO")
    assert RECOMMENDATION_FILTER_VALUES == ("GO", "CONDITIONAL_GO", "NO_GO", "none")
    assert RECOMMENDATION_INVALID_DETAIL == "recommendation must be one of GO, CONDITIONAL_GO, NO_GO, none."
    assert (MAX_VERDICT_RUN_IDS, MAX_SIGNATURE_RUN_IDS, SIGNATURE_ROWS_PER_RUN) == (25, 50, 500)
    assert RUN_IDS_INVALID_DETAIL.format(max=25) == "run_id: between 1 and 25 run ids."
