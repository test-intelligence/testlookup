# AIQ-P3 Design: Evidence + Confidence Scoring

Status: GO (architect, run 3). Additive, pure-local, offline-safe.

## Goal
EvidenceRef{source, ref_id, excerpt, strength weak|medium|strong, contribution 0-100};
final confidence = weighted aggregate, capped; rule conf>70 requires >=2 medium or
1 strong source; surface the breakdown.

## Files
Add:
- `backend/app/agents/evidence.py` — `EvidenceRef` model + `aggregate_confidence()` (pure-local, never raises, mirrors `consistency.py`).
- `backend/tests/test_evidence_confidence.py` — unit + property tests.

Modify:
- `backend/app/models/agent_contracts.py` — add optional `confidence_breakdown: Optional[dict]` to `AgentContractMetadata`; extend `validate_agent_contract` with keyword-only `structured_evidence=None`.
- `backend/app/agents/log_intelligence_agent.py` — first adopter (plain class, no BaseAgent guards).
- `backend/app/agents/release_risk_agent.py` — second adopter (BaseAgent; keep log_decision intact).
- `backend/tests/test_architectural_agent_contracts.py` — additive ratchet: assert `confidence_breakdown` field + cap invariant; add `evidence.py` to INFRA_ALLOWLIST.
- `CHANGELOG.md` — AIQ-P3 entry.

## EvidenceRef schema (Pydantic v2)
Fields: source:str="", ref_id:str="", excerpt:str="" (redact_text + truncate 240 + "…"),
strength: Literal["weak","medium","strong"]="weak", contribution:int=0 (clamp 0-100).
All coercion via `field_validator(mode="before")`; never raises. `as_legacy_dict()` -> model_dump(json).

## Aggregation
Weights weak=1, medium=2, strong=3. raw = round(Σ contribution*weight / Σ weight), clamp[0,100].
Cap rule: if raw>70 AND NOT(strong>=1 or medium>=2): final=70, cap_applied=True, cap_reason set.
Empty evidence -> (0, zero-breakdown). Filters non-EvidenceRef items. Never raises.
breakdown = {raw_confidence, final_confidence, cap_applied, cap_reason, strength_tally, per_source, evidence_count}.

## validate_agent_contract integration
New keyword-only `structured_evidence: Optional[list[EvidenceRef]]=None`.
- None -> identical legacy behavior; confidence_breakdown=None.
- Provided -> aggregate_confidence(); confidence_score from final unless explicit confidence passed;
  auto-populate evidence_refs from as_legacy_dict() if caller passed none; set confidence_breakdown.
Stays inside existing try/except -> never raises.

## Adopters
- log_intelligence: two EvidenceRef(source=distributed_trace/log_anomaly, strength=medium, contribution=70).
- release_risk: EvidenceRef(source=score_model, strength=strong, contribution=100-risk_score) + consistency as weak.

## Gates / risks
- Add `evidence.py` to INFRA_ALLOWLIST (shared helper, not analytic agent).
- `evidence.py` must NOT contain literal `consistency_check_failed`.
- Only additive fields/params -> ratchet required-set + >=12 floor unaffected.
- quality_gate: no print, structlog kwargs only, PII redacted at model boundary. No new baselines.

## Acceptance criteria
- Empty -> 0; weak/medium-only >70 raw caps to 70; 1 strong / 2 medium clears bar; weighted-mean exact.
- excerpt redacts tokens/email/IP and truncates >240.
- bad inputs never raise.
- validate_agent_contract(structured_evidence=...) stamps confidence_breakdown + list[dict] evidence_refs.
- new ratchet asserts confidence_breakdown field + cap invariant; all gates green.
