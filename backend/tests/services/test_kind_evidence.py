"""Evidence-checklist kind triage (AI-4) — assembly matrix + consumers.

Pins:
* every check contributes exactly one row, in the fixed EVIDENCE_CHECKS
  order, for every input combination (available / missing signals);
* a human correction agreeing with the derived kind PINS the confidence to
  PINNED_CONFIDENCE with basis ``human_corrected`` (the vocabulary addition);
  a disagreeing correction contradicts and never pins;
* un-pinned records carry basis ``heuristic_estimate`` — the checklist only
  re-weighs existing signals, honestly labeled — with the documented
  +SUPPORT_BONUS / -CONTRADICT_PENALTY weighing clamped to [0, ADJUSTED_CAP];
* ``kind_confidence_of`` prefers the checklist confidence over the raw
  classifier score (both in-flight ``_audit`` dicts and persisted
  routing_metadata blobs);
* ``kind_label_for_display`` applies the external display floor — below 60
  (or unknown kind/confidence) there is NO label at all;
* ``BASIS_HUMAN_CORRECTED`` is part of the shared basis vocabulary but is
  NOT a valid rules-engine band basis (a static table can't claim human
  confirmation).
"""
from __future__ import annotations

import pytest

from app.services.kind_evidence import (
    ADJUSTED_CAP,
    CHECK_CLASSIFIER,
    CHECK_HISTORY_PATTERN,
    CHECK_INFRA_SHAPE,
    CHECK_MEMORY_RECALL,
    CHECK_STATUS_SIGNAL,
    CONTRADICT_PENALTY,
    EVIDENCE_CHECKS,
    KIND_DISPLAY_CONFIDENCE_FLOOR,
    PINNED_CONFIDENCE,
    SUPPORT_BONUS,
    VERDICT_CONTRADICTS,
    VERDICT_NEUTRAL,
    VERDICT_SUPPORTS,
    VERDICT_UNAVAILABLE,
    assemble_kind_evidence,
    kind_confidence_of,
    kind_label_for_display,
    match_error_shape,
)


def _by_check(record: dict) -> dict[str, dict]:
    return {row["check"]: row for row in record["checks"]}


def _analysis(category: str = "INFRASTRUCTURE", confidence: int = 70, **extra) -> dict:
    return {"failure_category": category, "confidence_score": confidence, **extra}


# ── Structure ────────────────────────────────────────────────────────────────


class TestChecklistStructure:
    def test_every_check_present_in_fixed_order(self):
        record = assemble_kind_evidence(_analysis())
        assert [row["check"] for row in record["checks"]] == list(EVIDENCE_CHECKS)

    def test_minimal_inputs_degrade_to_unavailable_not_errors(self):
        """No recall, no error text, no history, no status — each check
        reports unavailable/neutral instead of raising."""
        record = assemble_kind_evidence(_analysis())
        checks = _by_check(record)
        assert checks[CHECK_MEMORY_RECALL]["verdict"] == VERDICT_UNAVAILABLE
        assert checks[CHECK_INFRA_SHAPE]["verdict"] == VERDICT_UNAVAILABLE
        assert checks[CHECK_HISTORY_PATTERN]["verdict"] == VERDICT_UNAVAILABLE
        assert checks[CHECK_STATUS_SIGNAL]["verdict"] == VERDICT_UNAVAILABLE
        assert checks[CHECK_CLASSIFIER]["verdict"] == VERDICT_SUPPORTS
        assert record["kind"] == "infrastructure"
        assert record["confidence"] == 70  # base, no adjustments
        assert record["confidence_basis"] == "heuristic_estimate"
        assert record["pinned_by_human_correction"] is False

    def test_malformed_confidence_degrades_to_zero(self):
        record = assemble_kind_evidence(_analysis(confidence="not-a-number"))
        assert record["classifier_confidence"] == 0


# ── memory_recall check + human-correction pin ───────────────────────────────


class TestMemoryRecallCheck:
    def test_agreeing_correction_pins_confidence_and_basis(self):
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE", confidence=40),
            correction={"corrected_category": "INFRASTRUCTURE", "corrected_at": "2026-07-01T00:00:00Z"},
        )
        assert record["pinned_by_human_correction"] is True
        assert record["confidence"] == PINNED_CONFIDENCE
        assert record["confidence_basis"] == "human_corrected"
        row = _by_check(record)[CHECK_MEMORY_RECALL]
        assert row["verdict"] == VERDICT_SUPPORTS
        assert "authoritative" in row["detail"]

    def test_disagreeing_correction_contradicts_and_never_pins(self):
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE", confidence=70),
            correction={"corrected_category": "PRODUCT_BUG"},
        )
        assert record["pinned_by_human_correction"] is False
        assert record["confidence_basis"] == "heuristic_estimate"
        row = _by_check(record)[CHECK_MEMORY_RECALL]
        assert row["verdict"] == VERDICT_CONTRADICTS
        assert record["confidence"] == 70 - CONTRADICT_PENALTY

    def test_prior_analyses_agreement_supports(self):
        record = assemble_kind_evidence(
            _analysis(),
            prior_analyses=[
                {"failure_category": "INFRASTRUCTURE"},
                {"failure_category": "INFRASTRUCTURE"},
            ],
        )
        assert _by_check(record)[CHECK_MEMORY_RECALL]["verdict"] == VERDICT_SUPPORTS

    def test_prior_analyses_disagreement_contradicts(self):
        record = assemble_kind_evidence(
            _analysis(),
            prior_analyses=[{"failure_category": "PRODUCT_BUG"}],
        )
        assert _by_check(record)[CHECK_MEMORY_RECALL]["verdict"] == VERDICT_CONTRADICTS

    def test_prior_analyses_mixed_is_neutral(self):
        record = assemble_kind_evidence(
            _analysis(),
            prior_analyses=[
                {"failure_category": "INFRASTRUCTURE"},
                {"failure_category": "PRODUCT_BUG"},
            ],
        )
        assert _by_check(record)[CHECK_MEMORY_RECALL]["verdict"] == VERDICT_NEUTRAL


# ── infra_shape check ────────────────────────────────────────────────────────


class TestInfraShapeCheck:
    def test_matching_shape_supports(self):
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE"),
            error_message="java.net.ConnectException: Connection refused",
        )
        row = _by_check(record)[CHECK_INFRA_SHAPE]
        assert row["verdict"] == VERDICT_SUPPORTS
        assert "pattern.connection_refused" in row["detail"]

    def test_mismatching_shape_contradicts(self):
        """Assertion-shaped error text vs an infrastructure verdict."""
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE"),
            error_message="AssertionError: expected 3 but was 4",
        )
        assert _by_check(record)[CHECK_INFRA_SHAPE]["verdict"] == VERDICT_CONTRADICTS

    def test_no_pattern_match_is_neutral(self):
        record = assemble_kind_evidence(
            _analysis(), error_message="something entirely novel happened",
        )
        assert _by_check(record)[CHECK_INFRA_SHAPE]["verdict"] == VERDICT_NEUTRAL

    def test_match_error_shape_reuses_rules_engine_patterns(self):
        assert match_error_shape("OOMKilled by the kernel") == ("pattern.oom", "INFRASTRUCTURE")
        assert match_error_shape("") is None
        assert match_error_shape(None) is None


# ── history_pattern check ────────────────────────────────────────────────────


class TestHistoryPatternCheck:
    def test_intermittent_supports_test_code(self):
        record = assemble_kind_evidence(
            _analysis(category="FLAKY"),
            history={"pass_count": 5, "fail_count": 5},
        )
        assert _by_check(record)[CHECK_HISTORY_PATTERN]["verdict"] == VERDICT_SUPPORTS

    def test_intermittent_contradicts_product(self):
        record = assemble_kind_evidence(
            _analysis(category="PRODUCT_BUG"),
            history={"pass_count": 5, "fail_count": 5},
        )
        assert _by_check(record)[CHECK_HISTORY_PATTERN]["verdict"] == VERDICT_CONTRADICTS

    def test_chronic_never_passing_contradicts_flaky(self):
        record = assemble_kind_evidence(
            _analysis(category="FLAKY"),
            history={"pass_count": 0, "fail_count": 6},
        )
        row = _by_check(record)[CHECK_HISTORY_PATTERN]
        assert row["verdict"] == VERDICT_CONTRADICTS
        assert "never passed" in row["detail"]

    def test_new_failure_against_passing_history_supports_product(self):
        record = assemble_kind_evidence(
            _analysis(category="PRODUCT_BUG"),
            history={"pass_count": 40, "fail_count": 1},
        )
        assert _by_check(record)[CHECK_HISTORY_PATTERN]["verdict"] == VERDICT_SUPPORTS

    def test_no_history_unavailable(self):
        record = assemble_kind_evidence(_analysis(), history={"pass_count": 0, "fail_count": 0})
        assert _by_check(record)[CHECK_HISTORY_PATTERN]["verdict"] == VERDICT_UNAVAILABLE


# ── status_signal check ──────────────────────────────────────────────────────


class TestStatusSignalCheck:
    def test_broken_supports_infrastructure(self):
        record = assemble_kind_evidence(_analysis(category="INFRASTRUCTURE"), status="BROKEN")
        assert _by_check(record)[CHECK_STATUS_SIGNAL]["verdict"] == VERDICT_SUPPORTS

    def test_failed_supports_product(self):
        record = assemble_kind_evidence(_analysis(category="PRODUCT_BUG"), status="FAILED")
        assert _by_check(record)[CHECK_STATUS_SIGNAL]["verdict"] == VERDICT_SUPPORTS

    def test_failed_neutral_for_infrastructure(self):
        record = assemble_kind_evidence(_analysis(category="INFRASTRUCTURE"), status="FAILED")
        assert _by_check(record)[CHECK_STATUS_SIGNAL]["verdict"] == VERDICT_NEUTRAL


# ── classifier check + weighing ──────────────────────────────────────────────


class TestWeighing:
    def test_supporting_checks_add_bonus_capped(self):
        """BROKEN status + infra-shaped error + agreeing priors → 3 supports."""
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE", confidence=70),
            status="BROKEN",
            error_message="connection refused by upstream",
            prior_analyses=[{"failure_category": "INFRASTRUCTURE"}],
        )
        assert record["confidence"] == min(70 + 3 * SUPPORT_BONUS, ADJUSTED_CAP)
        assert record["confidence_basis"] == "heuristic_estimate"

    def test_unpinned_confidence_never_exceeds_cap(self):
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE", confidence=100),
            status="BROKEN",
            error_message="connection refused",
        )
        assert record["confidence"] == ADJUSTED_CAP
        assert record["confidence"] < PINNED_CONFIDENCE

    def test_contradictions_floor_at_zero(self):
        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE", confidence=10),
            correction={"corrected_category": "PRODUCT_BUG"},
            history={"pass_count": 5, "fail_count": 5},  # neutral for infra
            error_message="AssertionError: expected true",  # contradicts
        )
        assert record["confidence"] == 0

    def test_unknown_kind_classifier_check_is_neutral(self):
        record = assemble_kind_evidence(_analysis(category="UNKNOWN", confidence=0))
        assert record["kind"] == "unknown"
        assert _by_check(record)[CHECK_CLASSIFIER]["verdict"] == VERDICT_NEUTRAL

    def test_broken_status_nudges_kind_like_failure_kind(self):
        """The record's kind uses the same BROKEN nudge as the triad."""
        record = assemble_kind_evidence(_analysis(category=None, confidence=0), status="BROKEN")
        assert record["kind"] == "infrastructure"


# ── kind_confidence_of ───────────────────────────────────────────────────────


class TestKindConfidenceOf:
    def test_prefers_audit_kind_evidence(self):
        analysis = {
            "confidence_score": 40,
            "_audit": {"kind_evidence": {"confidence": 85}},
        }
        assert kind_confidence_of(analysis) == 85

    def test_reads_persisted_routing_metadata_shape(self):
        routing_metadata = {"kind_evidence": {"confidence": 72}}
        assert kind_confidence_of({"confidence_score": 10, **routing_metadata}) == 72

    def test_falls_back_to_confidence_score(self):
        assert kind_confidence_of({"confidence_score": 55}) == 55

    def test_none_when_unknown(self):
        assert kind_confidence_of({}) is None
        assert kind_confidence_of(None) is None


# ── Display floor ────────────────────────────────────────────────────────────


class TestDisplayFloor:
    def test_at_floor_gets_label(self):
        label = kind_label_for_display("infrastructure", KIND_DISPLAY_CONFIDENCE_FLOOR)
        assert label == f"Infrastructure ({KIND_DISPLAY_CONFIDENCE_FLOOR}% conf, AI-classified)"

    def test_below_floor_no_label(self):
        assert kind_label_for_display("infrastructure", KIND_DISPLAY_CONFIDENCE_FLOOR - 1) is None

    @pytest.mark.parametrize("kind,confidence", [
        ("unknown", 99),      # never decorate with "Unknown"
        (None, 99),
        ("product", None),    # unknown confidence
    ])
    def test_noise_cases_no_label(self, kind, confidence):
        assert kind_label_for_display(kind, confidence) is None


# ── Basis vocabulary ─────────────────────────────────────────────────────────


class TestBasisVocabulary:
    def test_human_corrected_in_shared_vocab(self):
        from app.services.confidence_bands import (
            ALL_CONFIDENCE_BASES,
            BASIS_HUMAN_CORRECTED,
        )

        assert BASIS_HUMAN_CORRECTED == "human_corrected"
        assert BASIS_HUMAN_CORRECTED in ALL_CONFIDENCE_BASES

    def test_human_corrected_not_a_valid_band_basis(self):
        """A static rule table can never claim human confirmation."""
        from app.services.confidence_bands import BASIS_HUMAN_CORRECTED, ConfidenceBand

        with pytest.raises(ValueError):
            ConfidenceBand("rule.x", 50, BASIS_HUMAN_CORRECTED, "nope")

    def test_pin_matches_correction_confidence(self):
        """The checklist pin equals the learning-loop's correction confidence."""
        from app.services.analysis_corrections import build_corrected_analysis

        corrected = build_corrected_analysis({"corrected_category": "PRODUCT_BUG"})
        assert corrected["confidence_score"] == PINNED_CONFIDENCE


# ── Persistence round-trip (routing_metadata, AI-F4 pattern) ─────────────────


class TestPersistenceRoundTrip:
    def test_record_is_json_serializable_and_readable_back(self):
        """The record must survive the JSONB round-trip: what the pipeline
        stores under routing_metadata.kind_evidence is exactly what the
        gate/display helpers read back."""
        import json

        record = assemble_kind_evidence(
            _analysis(category="INFRASTRUCTURE", confidence=70),
            status="BROKEN",
            error_message="connection refused",
            correction={"corrected_category": "INFRASTRUCTURE", "corrected_at": "2026-07-01"},
        )
        routing_metadata = json.loads(json.dumps({"kind_evidence": record}))
        assert routing_metadata["kind_evidence"] == record
        # gate consumer reads the persisted shape
        assert kind_confidence_of({"confidence_score": 1, **routing_metadata}) == PINNED_CONFIDENCE
        # in-flight consumer reads the _audit shape (what _analyse_one stores)
        assert kind_confidence_of({"confidence_score": 1, "_audit": routing_metadata}) == PINNED_CONFIDENCE

    @pytest.mark.asyncio
    async def test_read_path_prefers_stored_blob(self):
        """compute_kind_evidence_for_test_case returns the persisted blob
        verbatim — no recompute, no extra queries."""
        import uuid
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from app.services.kind_evidence import compute_kind_evidence_for_test_case

        stored = {"schema_version": 1, "kind": "infrastructure", "confidence": 77,
                  "confidence_basis": "heuristic_estimate", "checks": []}
        ai = SimpleNamespace(
            id=uuid.uuid4(),
            routing_metadata={"kind_evidence": stored},
            failure_category="INFRASTRUCTURE",
            confidence_score=70,
        )
        tc = SimpleNamespace(status="FAILED", error_message=None, test_fingerprint=None)
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(first=MagicMock(
            return_value=(ai, tc, uuid.uuid4()),
        )))
        result = await compute_kind_evidence_for_test_case(db, uuid.uuid4())
        assert result == stored
        db.execute.assert_awaited_once()  # exactly the one lookup query

    @pytest.mark.asyncio
    async def test_read_path_computes_on_demand_when_blob_absent(self):
        """Pre-AI-4 row (no stored blob, no fingerprint) → assembled from
        the stored analysis + test-case columns; no backfill required."""
        import uuid
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from app.services.kind_evidence import compute_kind_evidence_for_test_case

        ai = SimpleNamespace(
            id=uuid.uuid4(),
            routing_metadata=None,
            failure_category="INFRASTRUCTURE",
            confidence_score=64,
        )
        tc = SimpleNamespace(
            status="BROKEN",
            error_message="connection refused by db",
            test_fingerprint=None,  # skips correction/history/prior fetches
        )
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(first=MagicMock(
            return_value=(ai, tc, uuid.uuid4()),
        )))
        result = await compute_kind_evidence_for_test_case(db, uuid.uuid4())
        assert result["kind"] == "infrastructure"
        assert result["confidence_basis"] == "heuristic_estimate"
        # BROKEN status + infra-shaped error both support → base 64 + 10
        assert result["confidence"] == 64 + 2 * SUPPORT_BONUS

    @pytest.mark.asyncio
    async def test_read_path_returns_none_when_no_analysis(self):
        import uuid
        from unittest.mock import AsyncMock, MagicMock

        from app.services.kind_evidence import compute_kind_evidence_for_test_case

        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
        assert await compute_kind_evidence_for_test_case(db, uuid.uuid4()) is None
