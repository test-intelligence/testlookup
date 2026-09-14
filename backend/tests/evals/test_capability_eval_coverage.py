"""E9.2 corpus coverage and mutation invariants."""
from __future__ import annotations

from app.services.agent_capability_registry import CAPABILITY_REGISTRY
from app.services.agent_eval_samples import (
    ANALYSIS_SAMPLE_FLOOR,
    CAPABILITY_EVAL_SAMPLES,
    EVAL_EXEMPTIONS,
    OUTPUT_CONTRACT_LABEL_KINDS,
    SMOKE_SAMPLE_FLOOR,
    MutationClass,
    generate_semantic_mutations,
)
from app.services.golden_agent_outputs import (
    eval_coverage_by_capability,
    get_golden_analysis_outputs,
)


def test_every_registered_capability_is_measured_or_explicitly_exempt() -> None:
    coverage = eval_coverage_by_capability()

    assert set(coverage) == set(CAPABILITY_REGISTRY)
    for capability, item in coverage.items():
        assert item["measured"] or item["eval_exempt"], capability
        if item["eval_exempt"]:
            assert item["sample_count"] == 0
            assert item["exemption_reason"]
        else:
            assert item["sample_count"] >= item["sample_floor"]


def test_analysis_has_one_hundred_samples_and_other_corpora_meet_smoke_floor() -> None:
    assert ANALYSIS_SAMPLE_FLOOR == 100
    assert SMOKE_SAMPLE_FLOOR == 20
    assert len(get_golden_analysis_outputs()) >= 100
    assert len(CAPABILITY_EVAL_SAMPLES["root_cause_analysis"]) >= 100
    for capability, samples in CAPABILITY_EVAL_SAMPLES.items():
        floor = 100 if capability == "root_cause_analysis" else 20
        assert len(samples) >= floor, capability


def test_samples_pin_registered_contracts_and_have_unique_inputs_and_labels() -> None:
    assert set(OUTPUT_CONTRACT_LABEL_KINDS) == {
        spec.output_schema for spec in CAPABILITY_REGISTRY.values()
    }
    all_ids: set[str] = set()
    for capability, samples in CAPABILITY_EVAL_SAMPLES.items():
        spec = CAPABILITY_REGISTRY[capability]
        assert {sample.input_schema for sample in samples} == {spec.input_schema}
        assert {sample.output_schema for sample in samples} == {spec.output_schema}
        assert len({sample.sample_id for sample in samples}) == len(samples)
        assert len({repr(sample.input_payload) for sample in samples}) == len(samples)
        all_ids.update(sample.sample_id for sample in samples)
    assert len(all_ids) == sum(len(samples) for samples in CAPABILITY_EVAL_SAMPLES.values())


def test_four_pilot_corpora_are_registered_as_samples() -> None:
    pilots = {
        "contract_validation", "log_intelligence", "regression_watchman", "decision_report"
    }
    for capability in pilots:
        samples = CAPABILITY_EVAL_SAMPLES[capability]
        assert len(samples) >= 20
        assert {sample.source for sample in samples} == {"pilot-corpus-v1"}


def test_semantic_mutations_are_labelled_applied_and_excluded_from_corpus() -> None:
    sample = CAPABILITY_EVAL_SAMPLES["root_cause_analysis"][0]
    mutations = generate_semantic_mutations(sample)

    assert {mutation.mutation_class for mutation in mutations} == set(MutationClass)
    original = sample.label.model_dump(mode="json")
    assert all(mutation.mutated_label != original for mutation in mutations)
    corpus_ids = {
        registered.sample_id
        for samples in CAPABILITY_EVAL_SAMPLES.values()
        for registered in samples
    }
    assert all(f"mutation:{mutation.sample_id}" not in corpus_ids for mutation in mutations)


def test_eval_exemptions_are_registered_and_reasoned() -> None:
    assert EVAL_EXEMPTIONS
    assert set(EVAL_EXEMPTIONS) <= set(CAPABILITY_REGISTRY)
    assert all(reason.strip() for reason in EVAL_EXEMPTIONS.values())
