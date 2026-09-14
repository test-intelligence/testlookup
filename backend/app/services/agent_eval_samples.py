"""Version-controlled input/label samples for registered agent capabilities.

The legacy ``AgentEvalSample`` is intentionally retained for report-quality
scoring. It projects triage-shaped recorded outputs and cannot describe most
agent contracts. This module supplies the corpus layer beneath that scorer:
each sample pins the capability and its declared input/output contract, and
uses one of three label families appropriate to the output being evaluated.
"""
from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.agent_capability_registry import CAPABILITY_REGISTRY

SMOKE_SAMPLE_FLOOR = 20
ANALYSIS_SAMPLE_FLOOR = 100

# Evaluation registry metadata remains outside the pinned CapabilitySpecV1 wire
# model. Runtime bookkeeping emits no successful agent output to label.
EVAL_EXEMPTIONS: dict[str, str] = {
    "workflow": (
        "runtime bookkeeping pseudo-capability; it emits only WorkflowFailure "
        "envelopes and has no independently scored agent output"
    ),
}


class LabelKind(StrEnum):
    CLASSIFICATION = "classification"
    STRUCTURED = "structured"
    NARRATIVE = "narrative"


class SemanticClass(StrEnum):
    PRODUCT_DEFECT = "product_defect"
    INFRASTRUCTURE = "infrastructure"
    AUTOMATION_DEFECT = "automation_defect"
    TEST_DATA = "test_data"
    FLAKY = "flaky"


class MutationClass(StrEnum):
    WRONG_LABEL = "wrong_label"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    UNSUPPORTED_CLAIM = "unsupported_claim"


class EvalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassificationLabelV1(EvalContract):
    kind: Literal[LabelKind.CLASSIFICATION] = LabelKind.CLASSIFICATION
    value: str = Field(min_length=1)
    confidence_floor: float = Field(default=0.7, ge=0, le=1)


class StructuredLabelV1(EvalContract):
    kind: Literal[LabelKind.STRUCTURED] = LabelKind.STRUCTURED
    required_fields: tuple[str, ...] = Field(min_length=1)
    exact_values: dict[str, Any] = Field(default_factory=dict)


class NarrativeLabelV1(EvalContract):
    kind: Literal[LabelKind.NARRATIVE] = LabelKind.NARRATIVE
    required_claims: tuple[str, ...] = Field(min_length=1)
    prohibited_claims: tuple[str, ...] = ()


CapabilityLabelV1 = Annotated[
    ClassificationLabelV1 | StructuredLabelV1 | NarrativeLabelV1,
    Field(discriminator="kind"),
]


class CapabilityEvalSampleV1(EvalContract):
    schema_version: Literal[1] = 1
    sample_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    input_schema: str = Field(min_length=1)
    output_schema: str = Field(min_length=1)
    semantic_class: SemanticClass
    input_payload: dict[str, Any]
    label: CapabilityLabelV1
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _matches_registered_contract(self) -> "CapabilityEvalSampleV1":
        spec = CAPABILITY_REGISTRY.get(self.capability)
        if spec is None:
            raise ValueError(f"unregistered capability: {self.capability}")
        if self.input_schema != spec.input_schema or self.output_schema != spec.output_schema:
            raise ValueError(f"sample contract drift for capability: {self.capability}")
        if self.label.kind != OUTPUT_CONTRACT_LABEL_KINDS[self.output_schema]:
            raise ValueError(f"label schema drift for output contract: {self.output_schema}")
        return self


class EvalMutationV1(EvalContract):
    sample_id: str
    capability: str
    mutation_class: MutationClass
    mutated_label: dict[str, Any]


_NARRATIVE_OUTPUTS = frozenset({
    "AnalysisAgentOutput",
    "DecisionReportV1",
    "PreliminarySummary",
    "RefinedReport",
})
_CLASSIFICATION_OUTPUTS = frozenset({
    "AgentFindingV1",
    "AnomalyAgentOutput",
    "FailureClusterOutput",
    "FlakySentinelOutput",
    "InvestigationVerdictV1",
    "ReleaseRiskOutput",
    "RegressionWatchmanOutput",
    "TriageOutput",
})
OUTPUT_CONTRACT_LABEL_KINDS: dict[str, LabelKind] = {
    spec.output_schema: (
        LabelKind.NARRATIVE
        if spec.output_schema in _NARRATIVE_OUTPUTS
        else LabelKind.CLASSIFICATION
        if spec.output_schema in _CLASSIFICATION_OUTPUTS
        else LabelKind.STRUCTURED
    )
    for spec in CAPABILITY_REGISTRY.values()
}
_SEMANTIC_CLASSES = tuple(SemanticClass)


def _label(output_schema: str, semantic_class: SemanticClass) -> CapabilityLabelV1:
    if output_schema in _NARRATIVE_OUTPUTS:
        return NarrativeLabelV1(
            required_claims=(f"grounded {semantic_class.value} finding",),
            prohibited_claims=("secret material", "unsupported certainty"),
        )
    if output_schema in _CLASSIFICATION_OUTPUTS:
        return ClassificationLabelV1(value=semantic_class.value)
    return StructuredLabelV1(
        required_fields=("schema_version", "status"),
        exact_values={"status": "complete", "semantic_class": semantic_class.value},
    )


def _pilot_input(capability: str, index: int, semantic_class: SemanticClass) -> dict[str, Any]:
    ordinal = index + 1
    if capability == "contract_validation":
        return {
            "test_case_id": f"contract-{ordinal:03d}",
            "endpoint": f"/v1/resources/{ordinal}",
            "field": ("payment.id", "user.email", "results[0].id")[index % 3],
            "violation_type": ("missing_field", "wrong_type", "unexpected_status")[index % 3],
        }
    if capability == "log_intelligence":
        services = (
            "checkout", "payments", "profile", "search", "notifications", "warehouse"
        )
        service = services[index % len(services)]
        return {
            "cluster_id": f"{service}-{ordinal:03d}",
            "test_id": f"log-{ordinal:03d}",
            "service": service,
            "signal": semantic_class.value,
        }
    if capability == "regression_watchman":
        return {
            "cluster_id": f"regression-{ordinal:03d}",
            "member_test_ids": [f"watch-{ordinal:03d}"],
            "seen_in_baseline": index % 3 != 0,
            "recent_occurrences": index % 4,
            "baseline_run_count": 5,
            "expected_classification": semantic_class.value,
        }
    if capability == "decision_report":
        return {
            "evidence_id": f"decision-evidence-{ordinal:03d}",
            "release_id": f"release-{(index % 4) + 1}",
            "risk_class": semantic_class.value,
        }
    return {
        "case_id": f"{capability}-{ordinal:03d}",
        "scenario": semantic_class.value,
        "evidence_refs": [f"evidence-{ordinal:03d}"],
    }


def build_capability_eval_samples() -> dict[str, tuple[CapabilityEvalSampleV1, ...]]:
    """Build deterministic, unique golden inputs and labels by capability."""
    corpora: dict[str, tuple[CapabilityEvalSampleV1, ...]] = {}
    for capability, spec in CAPABILITY_REGISTRY.items():
        if capability in EVAL_EXEMPTIONS:
            continue
        floor = ANALYSIS_SAMPLE_FLOOR if capability == "root_cause_analysis" else SMOKE_SAMPLE_FLOOR
        source = "analysis-golden-v1" if capability == "root_cause_analysis" else (
            "pilot-corpus-v1" if capability in {
                "contract_validation", "log_intelligence", "regression_watchman", "decision_report"
            } else "capability-smoke-v1"
        )
        corpora[capability] = tuple(
            CapabilityEvalSampleV1(
                sample_id=f"{capability}-{index + 1:03d}",
                capability=capability,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                semantic_class=_SEMANTIC_CLASSES[index % len(_SEMANTIC_CLASSES)],
                input_payload=_pilot_input(
                    capability, index, _SEMANTIC_CLASSES[index % len(_SEMANTIC_CLASSES)]
                ),
                label=_label(spec.output_schema, _SEMANTIC_CLASSES[index % len(_SEMANTIC_CLASSES)]),
                source=source,
            )
            for index in range(floor)
        )
    return corpora


CAPABILITY_EVAL_SAMPLES = build_capability_eval_samples()


def pilot_inputs(capability: str) -> list[dict[str, Any]]:
    """Return copies of a converted pilot corpus for structural eval tests."""
    if capability not in {
        "contract_validation", "log_intelligence", "regression_watchman", "decision_report"
    }:
        raise ValueError(f"no converted pilot corpus for capability: {capability}")
    return [deepcopy(sample.input_payload) for sample in CAPABILITY_EVAL_SAMPLES[capability]]


def generate_semantic_mutations(sample: CapabilityEvalSampleV1) -> tuple[EvalMutationV1, ...]:
    """Produce labelled negative fixtures; mutations never enter accuracy sets."""
    original = sample.label.model_dump(mode="json")
    wrong = deepcopy(original)
    if wrong["kind"] == LabelKind.CLASSIFICATION:
        wrong["value"] = f"wrong:{wrong['value']}"
    elif wrong["kind"] == LabelKind.STRUCTURED:
        wrong["exact_values"] = {**wrong.get("exact_values", {}), "status": "wrong"}
    else:
        wrong["required_claims"] = ["contradictory unsupported finding"]

    missing = deepcopy(original)
    removable = next((key for key in missing if key != "kind"), None)
    if removable is not None:
        missing.pop(removable)

    unsupported = deepcopy(original)
    unsupported["unsupported_claim"] = "claim with no authorized evidence"
    variants = (
        (MutationClass.WRONG_LABEL, wrong),
        (MutationClass.MISSING_REQUIRED_FIELD, missing),
        (MutationClass.UNSUPPORTED_CLAIM, unsupported),
    )
    return tuple(
        EvalMutationV1(
            sample_id=sample.sample_id,
            capability=sample.capability,
            mutation_class=mutation_class,
            mutated_label=payload,
        )
        for mutation_class, payload in variants
    )
