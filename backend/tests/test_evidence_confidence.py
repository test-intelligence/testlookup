"""
Regression coverage for the AIQ-P3 structured-evidence + confidence layer.

These tests are DB-free, pure-unit. They exercise:

  * ``app.agents.evidence.EvidenceRef`` — its never-raise field coercion and
    its redaction/truncation of excerpts.
  * ``app.agents.evidence.aggregate_confidence`` — the weighted-mean score,
    the strong/medium cap rule, the breakdown shape, and determinism.
  * ``app.models.agent_contracts.validate_agent_contract`` — that passing
    ``structured_evidence`` stamps ``confidence_breakdown`` and auto-fills
    ``evidence_refs``, that an explicit ``confidence`` wins, and that the
    helper never raises on junk evidence.
  * The two adopter modules (log_intelligence / release_risk) — verified at
    the source level that ``aggregate_confidence`` is wired in via
    ``validate_agent_contract(..., structured_evidence=...)``. A true unit
    invocation of those agents needs heavy third-party deps (langchain_core,
    prometheus_client) and async/DB fixtures, so the wiring is asserted
    statically rather than by importing the agents (see the relevant tests'
    docstrings for the skip rationale).

Run:
    cd /home/user/testlookup && PYTHONPATH=backend \
        python -m pytest backend/tests/test_evidence_confidence.py -q
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from app.agents.evidence import (
    EvidenceRef,
    aggregate_confidence,
)
from app.models.agent_contracts import (
    LogIntelligenceAgentOutput,
    RunCompareAgentOutput,
    validate_agent_contract,
)

AGENTS_DIR = Path(__file__).resolve().parents[1] / "app" / "agents"

ZERO_BREAKDOWN = {
    "raw_confidence": 0,
    "final_confidence": 0,
    "cap_applied": False,
    "cap_reason": "",
    "strength_tally": {"weak": 0, "medium": 0, "strong": 0},
    "evidence_count": 0,
    "per_source": [],
}


# ── 1. EvidenceRef validators never raise on junk ─────────────────────────────


@pytest.mark.parametrize(
    "junk",
    [
        None,
        {"nested": "dict"},
        ["a", "list"],
        3.14,
        True,
        False,
        float("inf"),
        float("-inf"),
        float("nan"),
        10 ** 50,  # huge int
        "héllo nön-ascii €",
        b"bytes",
        object(),
    ],
)
def test_evidenceref_never_raises_on_junk_per_field(junk):
    # Feed the junk into every field at once; construction must not raise.
    ref = EvidenceRef(
        source=junk,
        ref_id=junk,
        excerpt=junk,
        strength=junk,
        contribution=junk,
    )
    assert isinstance(ref.source, str)
    assert isinstance(ref.ref_id, str)
    assert isinstance(ref.excerpt, str)
    assert ref.strength in {"weak", "medium", "strong"}
    assert isinstance(ref.contribution, int)
    assert 0 <= ref.contribution <= 100


def test_evidenceref_missing_fields_use_defaults():
    ref = EvidenceRef()
    assert ref.source == ""
    assert ref.ref_id == ""
    assert ref.excerpt == ""
    assert ref.strength == "weak"
    assert ref.contribution == 0


def test_evidenceref_strength_coercion():
    assert EvidenceRef(strength="bogus").strength == "weak"
    assert EvidenceRef(strength=None).strength == "weak"
    assert EvidenceRef(strength=True).strength == "weak"
    assert EvidenceRef(strength=123).strength == "weak"
    # Valid values survive (case-insensitive).
    assert EvidenceRef(strength="STRONG").strength == "strong"
    assert EvidenceRef(strength="Medium").strength == "medium"
    assert EvidenceRef(strength="weak").strength == "weak"


def test_evidenceref_contribution_coercion_and_clamp():
    assert EvidenceRef(contribution="x").contribution == 0
    assert EvidenceRef(contribution=500).contribution == 100
    assert EvidenceRef(contribution=-5).contribution == 0
    assert EvidenceRef(contribution=float("inf")).contribution == 0
    assert EvidenceRef(contribution=float("-inf")).contribution == 0
    assert EvidenceRef(contribution=float("nan")).contribution == 0
    assert EvidenceRef(contribution=None).contribution == 0
    # In-range value survives, and a float in range truncates to int.
    assert EvidenceRef(contribution=55).contribution == 55
    assert EvidenceRef(contribution=42.9).contribution == 42


# ── 2. excerpt redaction + truncation ─────────────────────────────────────────


def test_excerpt_redacts_email_ip_and_bearer_token():
    raw_email = "john.doe@example.com"
    raw_ip = "192.168.1.100"
    raw_token = "abcdefghij1234567890XYZ"
    excerpt = (
        f"Contact {raw_email} at {raw_ip} with Bearer {raw_token} to authorize"
    )
    ref = EvidenceRef(excerpt=excerpt)
    # No raw secret/email/IP/token survives.
    assert raw_email not in ref.excerpt
    assert raw_ip not in ref.excerpt
    assert raw_token not in ref.excerpt
    # The redaction placeholders are present.
    assert "[REDACTED_EMAIL]" in ref.excerpt
    assert "[REDACTED_IP]" in ref.excerpt
    assert "[REDACTED]" in ref.excerpt


def test_excerpt_truncates_long_input_with_ellipsis():
    ref = EvidenceRef(excerpt="A" * 300)
    # 240 carried chars + single-char ellipsis.
    assert len(ref.excerpt) == 241
    assert ref.excerpt.endswith("…")
    assert ref.excerpt[:-1] == "A" * 240


def test_excerpt_exactly_at_limit_has_no_ellipsis():
    ref = EvidenceRef(excerpt="B" * 240)
    assert len(ref.excerpt) == 240
    assert not ref.excerpt.endswith("…")
    assert ref.excerpt == "B" * 240


# ── 3. aggregate_confidence degenerate inputs -> (0, zero-breakdown) ───────────


@pytest.mark.parametrize(
    "bad",
    [
        [],
        None,
        12345,  # non-iterable int
        3.14,
        "a string is iterable but holds no EvidenceRefs",
        [None, "x", 123, object()],  # iterable of junk, zero valid refs
        {"a": 1},  # dict is not list/tuple/set
    ],
)
def test_aggregate_confidence_degenerate_inputs(bad):
    score, breakdown = aggregate_confidence(bad)
    assert score == 0
    assert breakdown == ZERO_BREAKDOWN


# ── 4. cap boundary ───────────────────────────────────────────────────────────


def test_cap_raw_exactly_70_not_capped():
    score, breakdown = aggregate_confidence([
        EvidenceRef(strength="weak", contribution=70),
        EvidenceRef(strength="weak", contribution=70),
    ])
    assert breakdown["raw_confidence"] == 70
    assert score == 70
    assert breakdown["cap_applied"] is False
    assert breakdown["cap_reason"] == ""


def test_cap_raw_71_two_weak_capped_to_70():
    score, breakdown = aggregate_confidence([
        EvidenceRef(strength="weak", contribution=71),
        EvidenceRef(strength="weak", contribution=71),
    ])
    assert breakdown["raw_confidence"] == 71
    assert score == 70
    assert breakdown["cap_applied"] is True
    assert breakdown["cap_reason"]  # non-empty


def test_cap_one_medium_at_100_capped_to_70():
    # One medium is insufficient strength (rule needs >=2 medium or >=1 strong).
    score, breakdown = aggregate_confidence([
        EvidenceRef(strength="medium", contribution=100),
    ])
    assert breakdown["raw_confidence"] == 100
    assert score == 70
    assert breakdown["cap_applied"] is True


def test_cap_two_medium_at_100_not_capped():
    score, breakdown = aggregate_confidence([
        EvidenceRef(strength="medium", contribution=100),
        EvidenceRef(strength="medium", contribution=100),
    ])
    assert breakdown["raw_confidence"] == 100
    assert score == 100
    assert breakdown["cap_applied"] is False


def test_cap_one_strong_at_90_not_capped():
    score, breakdown = aggregate_confidence([
        EvidenceRef(strength="strong", contribution=90),
    ])
    assert breakdown["raw_confidence"] == 90
    assert score == 90
    assert breakdown["cap_applied"] is False


# ── 5. weighted-mean exactness (table-driven) ─────────────────────────────────


@pytest.mark.parametrize(
    "refs, expected_raw, note",
    [
        # strong@90 (w=3) + weak@70 (w=1): (270 + 70) / 4 = 340/4 = 85.0 -> 85.
        # (The phase brief's "82.5" was illustrative; the true weighted mean
        #  here is exactly 85.0, no rounding needed.)
        (
            [("strong", 90), ("weak", 70)],
            85,
            "exact 85.0",
        ),
        # medium@60 (w=2) + medium@80 (w=2): (120 + 160) / 4 = 280/4 = 70.0.
        (
            [("medium", 60), ("medium", 80)],
            70,
            "exact 70.0",
        ),
        # Banker's rounding (round-half-to-even) on a true .5 tie:
        # weak@70 + weak@71 = 141/2 = 70.5 -> 70 (rounds to even).
        (
            [("weak", 70), ("weak", 71)],
            70,
            "70.5 -> 70 (round-half-to-even)",
        ),
        # weak@71 + weak@72 = 143/2 = 71.5 -> 72 (rounds to even).
        (
            [("weak", 71), ("weak", 72)],
            72,
            "71.5 -> 72 (round-half-to-even)",
        ),
    ],
)
def test_weighted_mean_exactness(refs, expected_raw, note):
    score, breakdown = aggregate_confidence(
        [EvidenceRef(strength=s, contribution=c) for s, c in refs]
    )
    assert breakdown["raw_confidence"] == expected_raw, note
    # Sanity: Python's round is banker's rounding, which is what the source uses.
    assert round(70.5) == 70 and round(71.5) == 72


# ── 6. breakdown structure ────────────────────────────────────────────────────


def test_breakdown_structure_keys_and_tally():
    refs = [
        EvidenceRef(source="s1", ref_id="r1", strength="weak", contribution=40),
        EvidenceRef(source="s2", ref_id="r2", strength="medium", contribution=60),
        EvidenceRef(source="s3", ref_id="r3", strength="strong", contribution=90),
    ]
    score, breakdown = aggregate_confidence(refs)

    required_keys = {
        "raw_confidence",
        "final_confidence",
        "cap_applied",
        "cap_reason",
        "strength_tally",
        "evidence_count",
        "per_source",
    }
    assert required_keys <= set(breakdown)

    assert breakdown["strength_tally"] == {"weak": 1, "medium": 1, "strong": 1}
    assert breakdown["evidence_count"] == 3
    assert len(breakdown["per_source"]) == breakdown["evidence_count"]
    assert breakdown["final_confidence"] == score

    # per_source carries the right shape for each ref.
    for entry, ref in zip(breakdown["per_source"], refs):
        assert entry["source"] == ref.source
        assert entry["ref_id"] == ref.ref_id
        assert entry["strength"] == ref.strength
        assert entry["contribution"] == ref.contribution


def test_breakdown_cap_reason_only_nonempty_when_capped():
    # Not capped -> cap_reason empty.
    _, not_capped = aggregate_confidence([
        EvidenceRef(strength="strong", contribution=90),
    ])
    assert not_capped["cap_applied"] is False
    assert not_capped["cap_reason"] == ""

    # Capped -> cap_reason non-empty.
    _, capped = aggregate_confidence([
        EvidenceRef(strength="weak", contribution=95),
    ])
    assert capped["cap_applied"] is True
    assert capped["cap_reason"] != ""


# ── 7. determinism ────────────────────────────────────────────────────────────


def test_aggregate_confidence_is_deterministic():
    refs = [
        EvidenceRef(source="a", strength="strong", contribution=88),
        EvidenceRef(source="b", strength="medium", contribution=72),
        EvidenceRef(source="c", strength="weak", contribution=51),
    ]
    first = aggregate_confidence(refs)
    second = aggregate_confidence(refs)
    assert first == second
    # Score and full breakdown identical.
    assert first[0] == second[0]
    assert first[1] == second[1]


# ── 8. integration via validate_agent_contract ────────────────────────────────


def _evidence_for_strong_score():
    return [
        EvidenceRef(source="trace", ref_id="t1", strength="strong", contribution=90),
        EvidenceRef(source="log", ref_id="l1", strength="medium", contribution=70),
    ]


def test_validate_contract_stamps_breakdown_and_fills_evidence_refs():
    result = validate_agent_contract(
        LogIntelligenceAgentOutput,
        {"log_summary": "x"},
        agent_name="log_intelligence",
        structured_evidence=_evidence_for_strong_score(),
    )
    contract = result["agent_contracts"]["log_intelligence"]

    assert contract["confidence_breakdown"] is not None
    assert contract["confidence_breakdown"]["evidence_count"] == 2
    # Derived score flows into confidence_score.
    assert contract["confidence_score"] == contract["confidence_breakdown"]["final_confidence"]
    # evidence_refs auto-populated as a list of plain dicts.
    assert isinstance(contract["evidence_refs"], list)
    assert len(contract["evidence_refs"]) == 2
    assert all(isinstance(r, dict) for r in contract["evidence_refs"])
    assert contract["evidence_count"] == 2


def test_validate_contract_no_structured_evidence_leaves_breakdown_none():
    result = validate_agent_contract(
        RunCompareAgentOutput,
        {"status": "ready"},
        agent_name="run_compare",
        confidence=55,
        evidence_refs=[{"type": "x", "id": "y"}],
        decision_reason="t",
    )
    contract = result["agent_contracts"]["run_compare"]
    assert contract["confidence_breakdown"] is None
    # evidence_refs unchanged from what the caller passed.
    assert contract["evidence_refs"] == [{"type": "x", "id": "y"}]
    assert contract["evidence_count"] == 1
    assert contract["confidence_score"] == 55


def test_validate_contract_explicit_confidence_overrides_aggregated():
    result = validate_agent_contract(
        LogIntelligenceAgentOutput,
        {"log_summary": "x"},
        agent_name="log_intelligence",
        confidence=12,  # explicit wins over derived (would be ~83)
        structured_evidence=_evidence_for_strong_score(),
    )
    contract = result["agent_contracts"]["log_intelligence"]
    assert contract["confidence_score"] == 12
    # Breakdown is still stamped for audit even though confidence was explicit.
    assert contract["confidence_breakdown"] is not None
    assert contract["confidence_breakdown"]["evidence_count"] == 2


def test_validate_contract_never_raises_on_non_iterable_structured_evidence():
    # structured_evidence=12345 is truthy + non-iterable. aggregate_confidence
    # itself returns (0, zero-breakdown), but the subsequent evidence_refs
    # auto-fill comprehension iterates the int and raises; that is caught by the
    # helper's defense-in-depth try/except, which logs a warning and leaves the
    # breakdown None. The hard invariant under test is only that the helper
    # NEVER raises and still returns a well-formed contract.
    result = validate_agent_contract(
        LogIntelligenceAgentOutput,
        {"log_summary": "x"},
        agent_name="log_intelligence",
        structured_evidence=12345,
    )
    contract = result["agent_contracts"]["log_intelligence"]
    # No crash: a contract was produced. The breakdown degrades to None and
    # confidence falls back to 0 with no auto-filled evidence refs.
    assert contract["confidence_breakdown"] is None
    assert contract["confidence_score"] == 0
    assert contract["evidence_refs"] == []


# ── 9. adopters: aggregate_confidence is wired (source-level, DB-free) ─────────
#
# A true unit invocation of LogIntelligenceAgent / ReleaseRiskAgent pulls in
# heavy third-party deps (langchain_core, prometheus_client) and async/DB
# fixtures that this DB-free suite deliberately avoids, so the wiring is
# asserted at the source level via AST + text inspection rather than by
# importing and running the agents. This still guarantees that a changed
# evidence.py contract has matching coverage for its adopters.


@pytest.mark.parametrize(
    "filename",
    ["log_intelligence_agent.py", "release_risk_agent.py"],
)
def test_adopter_wires_structured_evidence_into_contract(filename):
    source = (AGENTS_DIR / filename).read_text(encoding="utf-8")

    # Imports EvidenceRef from the shared evidence module.
    assert "from app.agents.evidence import" in source
    assert "EvidenceRef" in source

    tree = ast.parse(source)

    # Calls validate_agent_contract with a structured_evidence keyword.
    wired = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if name != "validate_agent_contract":
            continue
        if any(kw.arg == "structured_evidence" for kw in node.keywords):
            wired = True
            break

    assert wired, (
        f"{filename} must call validate_agent_contract(..., "
        "structured_evidence=...) to route evidence through "
        "aggregate_confidence."
    )


def test_adopter_constructs_evidence_refs():
    # Both adopters should actually build EvidenceRef objects (not just import).
    for filename in ("log_intelligence_agent.py", "release_risk_agent.py"):
        source = (AGENTS_DIR / filename).read_text(encoding="utf-8")
        tree = ast.parse(source)
        constructs = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "EvidenceRef"
            for node in ast.walk(tree)
        )
        assert constructs, f"{filename} should construct EvidenceRef(...) instances."


def test_aggregate_confidence_is_importable_and_callable():
    # Smoke: the helper the adopters route through is importable and returns the
    # documented (int, dict) contract shape.
    from app.agents.evidence import aggregate_confidence as agg

    score, breakdown = agg([EvidenceRef(strength="strong", contribution=80)])
    assert isinstance(score, int)
    assert isinstance(breakdown, dict)
    assert not isinstance(score, bool)
    assert not math.isnan(float(score))
