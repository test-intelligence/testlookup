from __future__ import annotations

import importlib.util
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import agent_config_service as configs
from app.services.agent_eval_samples import CAPABILITY_EVAL_SAMPLES, LabelKind
from app.services.eval_verdict import EvalVerdict
from app.services import tier_comparison_service as svc


PROJECT_ID = uuid.uuid4()
AGENT_ID = "agent.summary.v1"


def _output(sample):
    label = sample.label
    if label.kind == LabelKind.CLASSIFICATION:
        return {"category": label.value}
    if label.kind == LabelKind.STRUCTURED:
        out = {field: "present" for field in label.required_fields}
        out.update(label.exact_values)
        return out
    return "Evidence: " + "; ".join(label.required_claims)


def _pairs(capability: str, count: int | None = None):
    samples = CAPABILITY_EVAL_SAMPLES[capability]
    if count is not None:
        samples = samples[:count]
    return [
        svc.TierOutputPair(
            sample_id=sample.sample_id,
            incumbent_output=_output(sample),
            candidate_output=_output(sample),
            incumbent_cost_usd=0.02,
            candidate_cost_usd=0.01,
            incumbent_latency_ms=40,
            candidate_latency_ms=20,
        )
        for sample in samples
    ]


def test_paired_summary_smoke_gate_passes_and_reports_ci_cost_and_latency():
    result = svc.compare_tier_outputs(
        capability="summary",
        incumbent_tier="llm",
        candidate_tier="slm",
        pairs=_pairs("summary"),
    )

    assert result["verdict"] == EvalVerdict.PASS.value
    assert result["smoke_mode"] is True and result["sample_count"] == 20
    metrics = {metric["name"]: metric for metric in result["per_gate"]["G2"]}
    assert metrics["paired_accuracy_delta"]["ci_low"] == 0
    assert metrics["cost_delta_usd"]["value"] == pytest.approx(-0.01)
    assert metrics["latency_delta_ms"]["value"] == -20


def test_comparison_is_insufficient_below_the_capability_floor():
    result = svc.compare_tier_outputs(
        capability="summary", incumbent_tier="llm", candidate_tier="slm", pairs=_pairs("summary", 19)
    )
    assert result["verdict"] == EvalVerdict.INSUFFICIENT_SAMPLES.value
    assert "at least 20" in result["reason"]


def test_comparison_fails_when_candidate_breaks_the_output_contract():
    pairs = _pairs("summary")
    pairs[0] = svc.TierOutputPair(
        sample_id=pairs[0].sample_id,
        incumbent_output=pairs[0].incumbent_output,
        candidate_output={},
    )
    result = svc.compare_tier_outputs(
        capability="summary", incumbent_tier="llm", candidate_tier="slm", pairs=pairs
    )
    assert result["verdict"] == EvalVerdict.FAIL.value
    assert "contract-valid" in result["reason"]


def test_analysis_gate_uses_the_paired_non_inferiority_bound():
    pairs = _pairs("root_cause_analysis")
    for index in range(6):
        pair = pairs[index]
        pairs[index] = svc.TierOutputPair(
            sample_id=pair.sample_id,
            incumbent_output=pair.incumbent_output,
            # Still contract-valid (required claim present), but incorrect due
            # to the prohibited unsupported disclosure.
            candidate_output=f"{pair.candidate_output}; secret material",
        )
    result = svc.compare_tier_outputs(
        capability="root_cause_analysis", incumbent_tier="llm", candidate_tier="slm", pairs=pairs
    )
    assert result["smoke_mode"] is False
    assert result["verdict"] == EvalVerdict.FAIL.value
    assert result["regressions"][0]["ci_low"] < -0.05


def test_duplicate_or_cross_capability_pairs_are_rejected():
    pair = _pairs("summary", 1)[0]
    with pytest.raises(ValueError, match="duplicate"):
        svc.compare_tier_outputs(
            capability="summary", incumbent_tier="llm", candidate_tier="slm", pairs=[pair, pair]
        )
    wrong = _pairs("triage", 1)[0]
    with pytest.raises(ValueError, match="not in"):
        svc.compare_tier_outputs(
            capability="summary", incumbent_tier="llm", candidate_tier="slm", pairs=[wrong]
        )


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _DB:
    def __init__(self, evidence=None, used=0):
        self.evidence = evidence
        self.used = used
        self.added = []
        self.events = []

    async def execute(self, _query):
        self.events.append("execute")
        return _Result(self.evidence)

    async def scalar(self, _query):
        self.events.append("scalar")
        return self.used

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()


async def test_config_hook_refuses_unmeasured_downgrade_but_allows_unmeasured_upgrade():
    llm = configs.default_config(AGENT_ID).model_copy(
        update={"model": configs.ModelConfig(tier="llm")}
    )
    slm = llm.model_copy(update={"model": configs.ModelConfig(tier="slm")})
    with pytest.raises(svc.TierComparisonRejected) as exc:
        await svc.enforce_config_tier_gate(
            _DB(), project_id=PROJECT_ID, agent_id=AGENT_ID, before=llm, after=slm
        )
    assert exc.value.report["verdict"] == EvalVerdict.INSUFFICIENT_SAMPLES.value

    await svc.enforce_config_tier_gate(
        _DB(), project_id=PROJECT_ID, agent_id=AGENT_ID, before=slm, after=llm
    )


async def test_auto_uses_the_capability_default_for_quality_direction():
    automatic = configs.default_config(AGENT_ID)
    llm = automatic.model_copy(update={"model": configs.ModelConfig(tier="llm")})
    slm = automatic.model_copy(update={"model": configs.ModelConfig(tier="slm")})
    deterministic = automatic.model_copy(
        update={"model": configs.ModelConfig(tier="deterministic")}
    )
    # T7 promotes summary's measured default to SLM.  Pinning that tier is a
    # no-op and pinning LLM is an upgrade; deterministic remains a downgrade.
    await svc.enforce_config_tier_gate(
        _DB(), project_id=PROJECT_ID, agent_id=AGENT_ID, before=automatic, after=slm
    )
    await svc.enforce_config_tier_gate(
        _DB(), project_id=PROJECT_ID, agent_id=AGENT_ID, before=automatic, after=llm
    )
    with pytest.raises(svc.TierComparisonRejected):
        await svc.enforce_config_tier_gate(
            _DB(), project_id=PROJECT_ID, agent_id=AGENT_ID, before=automatic, after=deterministic
        )


async def test_config_hook_refuses_a_failed_upgrade_and_accepts_a_passing_downgrade():
    slm = configs.default_config(AGENT_ID).model_copy(
        update={"model": configs.ModelConfig(tier="slm")}
    )
    llm = slm.model_copy(update={"model": configs.ModelConfig(tier="llm")})
    failed = SimpleNamespace(id=uuid.uuid4(), status=EvalVerdict.FAIL.value)
    with pytest.raises(svc.TierComparisonRejected):
        await svc.enforce_config_tier_gate(
            _DB(failed), project_id=PROJECT_ID, agent_id=AGENT_ID, before=slm, after=llm
        )
    passed = SimpleNamespace(id=uuid.uuid4(), status=EvalVerdict.PASS.value)
    await svc.enforce_config_tier_gate(
        _DB(passed), project_id=PROJECT_ID, agent_id=AGENT_ID, before=llm, after=slm
    )


async def test_persistence_stamps_typed_g2_manifest_columns():
    db = _DB()
    result = svc.compare_tier_outputs(
        capability="summary", incumbent_tier="llm", candidate_tier="slm", pairs=_pairs("summary")
    )
    row = await svc.persist_tier_comparison(
        db, project_id=PROJECT_ID, agent_id=AGENT_ID, result=result, evaluated_by=None
    )
    assert row.gate_type == "tier_comparison" and row.project_id == PROJECT_ID
    assert row.candidate_tier == "slm" and row.sample_count == 20


async def test_active_drift_pin_blocks_even_a_previously_passing_downgrade(monkeypatch):
    async def pinned(_db, _project_id, _agent_id):
        return True

    from app.services import online_drift_service

    monkeypatch.setattr(online_drift_service, "has_active_drift_pin", pinned)
    llm = configs.default_config(AGENT_ID).model_copy(
        update={"model": configs.ModelConfig(tier="llm")}
    )
    slm = llm.model_copy(update={"model": configs.ModelConfig(tier="slm")})
    passed = SimpleNamespace(id=uuid.uuid4(), status=EvalVerdict.PASS.value)

    with pytest.raises(svc.TierComparisonRejected, match="eval-drift review"):
        await svc.enforce_config_tier_gate(
            _DB(passed), project_id=PROJECT_ID, agent_id=AGENT_ID, before=llm, after=slm
        )


async def test_shadow_pair_is_pending_and_daily_budget_is_hard():
    db = _DB(used=90)
    refused = await svc.store_shadow_pair(
        db,
        project_id=PROJECT_ID,
        agent_id=AGENT_ID,
        sample_key="run:1",
        incumbent_tier="llm",
        candidate_tier="slm",
        incumbent_output={"x": 1},
        candidate_output={"x": 1},
        incumbent_tokens=6,
        candidate_tokens=5,
        daily_token_budget=100,
    )
    assert refused is None and db.added == []
    db.used = 89
    row = await svc.store_shadow_pair(
        db,
        project_id=PROJECT_ID,
        agent_id=AGENT_ID,
        sample_key="run:1",
        incumbent_tier="llm",
        candidate_tier="slm",
        incumbent_output={"x": 1},
        candidate_output={"x": 1},
        incumbent_tokens=6,
        candidate_tokens=5,
        daily_token_budget=100,
    )
    assert row is not None and row.label_status == "pending" and row.total_tokens == 11
    assert db.events[-2:] == ["execute", "scalar"], "advisory lock must precede the budget read"


def test_shadow_enqueue_is_sampled_bounded_and_explicitly_on_default_queue(monkeypatch):
    from app.worker import tasks

    # Celery's apply_async is synchronous; use a small recorder instead.
    recorder = SimpleNamespace(id="task-1")
    call = SimpleNamespace(calls=[])

    def apply_async(**kwargs):
        call.calls.append(kwargs)
        return recorder

    monkeypatch.setattr(tasks.persist_ai_eval_shadow_pair, "apply_async", apply_async)
    config = configs.default_config(AGENT_ID).model_copy(
        update={"shadow": configs.ShadowConfig(sample_rate=1.0, daily_token_budget=10)}
    )
    assert svc.enqueue_shadow_pair(
        config=config,
        project_id=PROJECT_ID,
        agent_id=AGENT_ID,
        sample_key="run:1",
        incumbent_tier="llm",
        candidate_tier="slm",
        incumbent_output={},
        candidate_output={},
        incumbent_tokens=5,
        candidate_tokens=5,
    ) == "task-1"
    assert call.calls[0]["queue"] == "default"
    assert svc.enqueue_shadow_pair(
        config=config,
        project_id=PROJECT_ID,
        agent_id=AGENT_ID,
        sample_key="run:2",
        incumbent_tier="llm",
        candidate_tier="slm",
        incumbent_output={},
        candidate_output={},
        incumbent_tokens=6,
        candidate_tokens=5,
    ) is None


def test_migration_is_next_head_reversible_and_concurrent_for_existing_table():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0180_ai_eval_tier_comparison.py"
    spec = importlib.util.spec_from_file_location("m0180", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0180" and migration.down_revision == "0179"
    up = inspect.getsource(migration.upgrade)
    down = inspect.getsource(migration.downgrade)
    assert "autocommit_block" in up and "postgresql_concurrently=True" in up
    assert "if_not_exists=True" in up
    assert "drop_table(PAIR_TABLE)" in down and "drop_column" in down
    assert "postgresql_concurrently=True" in down and "if_exists=True" in down
