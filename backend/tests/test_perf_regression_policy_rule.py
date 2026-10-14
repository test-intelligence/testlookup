"""EXEC-05 / owner decision D44: a slowdown may block a release ONLY through
the opt-in ``perf_regression`` policy rule, which no existing policy has.

* Golden: every system / seeded policy shape, over every golden release item,
  evaluates byte-identically before and after the rule type exists -- before =
  the evaluator registry without ``perf_regression`` and a context without
  duration findings; after = today's registry and a context carrying two
  spikes and a suite regression.
* The rule itself: enabled + 2 spikes -> NO_GO "2 tests slower than baseline";
  WARN only turns GO into CONDITIONAL_GO; a run whose durations were not
  measured passes and says so; ``min_sigma`` / ``suite_ratio`` tighten.
* The release risk agent attaches the run's stored findings only when there
  are some, and its failure leaves the context exactly as it was.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import policy_evaluator_service as pe

DIMENSIONS = (
    "user_impact", "env_sensitivity", "reproducibility", "regression_likely",
    "hist_recurrence", "blast_radius", "diagnosis_conf",
)

TWO_SPIKES = {
    "measured": True,
    "reason": None,
    "spike_count": 2,
    "spikes": [
        {"test_name": "checkout::test_pay", "suite_name": "checkout", "sigma": 4.1,
         "observed_ms": 400, "baseline_mean_ms": 100.0},
        {"test_name": "checkout::test_cart", "suite_name": "checkout", "sigma": 3.4,
         "observed_ms": 260, "baseline_mean_ms": 120.0},
    ],
    "suite_regression_count": 1,
    "suites": [{"suite_name": "checkout", "ratio": 1.8}],
}


def _policy(doc: dict) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.UUID("44444444-4444-4444-8444-444444444444"), version=3, rules=doc)


def _system_policies() -> dict[str, dict]:
    """Every policy shape a deployment has without anyone adding the rule:
    the agent's frozen hardcoded fallback, the document a new policy starts
    from, each rule template the editor offers, and kind weighting on."""
    from app.core.config import settings
    from app.models.schemas import PolicyDocument
    from app.services.criticality_service import HARD_FLOOR_FACTOR, _weights

    hardcoded = {
        "thresholds": {
            "go_threshold": 20.0,
            "no_go_threshold": 55.0,
            "pass_rate_minimum": settings.RELEASE_PASS_RATE_THRESHOLD,
            "pass_rate_hard_floor_factor": HARD_FLOOR_FACTOR,
        },
        "dimension_weights": _weights(),
        "rules": [],
    }
    default_doc = PolicyDocument().model_dump()
    templates = deepcopy(default_doc)
    templates["rules"] = [
        {"id": "flaky", "name": "Flaky Recurrence Limit", "type": "flaky_recurrence",
         "enabled": True, "params": {"max_flaky_tests": 2, "action": "BLOCK"}},
        {"id": "defects", "name": "Open Defect Limit", "type": "open_defect_limit",
         "enabled": True, "params": {"max_open_defects": 5, "action": "WARN"}},
        {"id": "ceiling", "name": "Dimension Ceiling", "type": "dimension_ceiling",
         "enabled": True, "params": {"dimension": "regression_likely", "max_score": 70, "action": "BLOCK"}},
        {"id": "override", "name": "Override Restrictions", "type": "override_rules",
         "enabled": True, "params": {"allow_override_to_go_from_no_go": False}},
    ]
    kind = deepcopy(default_doc)
    kind["kind_rules"] = {"enabled": True, "infrastructure": {"max_failures": 3}}
    return {"hardcoded": hardcoded, "new_policy_default": default_doc,
            "every_rule_template": templates, "kind_weighting": kind}


def _golden_inputs():
    from app.services.golden_datasets import get_golden_release_decision_items

    for item in get_golden_release_decision_items():
        data = item["input"]
        dims = {d: float(data["risk_score"]) for d in DIMENSIONS}
        context = {
            "flaky_count": data["flaky_count"],
            "open_defects": data.get("critical_failures", 0),
            "regression_test_count": data.get("regression_count", 0),
            "failure_kind_counts": {"product": data["failed_tests"], "test_code": 0,
                                    "infrastructure": 0, "unknown": 0},
        }
        yield item["metadata"]["label"], dims, float(data["pass_rate"]), context


async def _evaluate(doc: dict, dims: dict, pass_rate: float, context: dict):
    return await pe.evaluate_policy(
        project_id=None, dim_scores=dims, pass_rate=pass_rate, context=context,
        db=None, policy_override=_policy(doc),
    )


def _comparable(result) -> dict:
    out = result.to_dict()
    out.pop("evaluated_at")
    return out


@pytest.mark.asyncio
async def test_every_system_policy_gives_the_identical_verdict_before_and_after_the_rule():
    before_registry = {k: v for k, v in pe._RULE_EVALUATORS.items() if k != "perf_regression"}
    assert len(before_registry) == len(pe._RULE_EVALUATORS) - 1
    checked = 0
    for name, doc in _system_policies().items():
        assert all(r.get("type") != "perf_regression" for r in doc.get("rules", [])), name
        for label, dims, pass_rate, context in _golden_inputs():
            with patch.dict(pe._RULE_EVALUATORS, clear=True, values=before_registry):
                before = await _evaluate(doc, dims, pass_rate, dict(context))
            after = await _evaluate(doc, dims, pass_rate, {**context, "duration_regressions": TWO_SPIKES})
            assert _comparable(after) == _comparable(before), (name, label)
            checked += 1
    assert checked == 4 * 6


@pytest.mark.asyncio
async def test_the_golden_items_keep_their_expected_verdicts_under_the_default_policy():
    from app.services.golden_datasets import get_golden_release_decision_items

    doc = _system_policies()["hardcoded"]
    expected = {i["metadata"]["label"]: i["expected_output"]["recommendation"]
                for i in get_golden_release_decision_items()}
    for label, dims, pass_rate, context in _golden_inputs():
        result = await _evaluate(doc, dims, pass_rate, {**context, "duration_regressions": TWO_SPIKES})
        assert result.recommendation == expected[label], label
        assert all(ev.rule_type != "perf_regression" for ev in result.rule_evaluations)


def _with_rule(**params) -> dict:
    from app.models.schemas import PolicyDocument

    doc = PolicyDocument().model_dump()
    doc["rules"] = [{"id": "perf", "name": "Performance regression", "type": "perf_regression",
                     "enabled": True, "params": params}]
    return doc


GO_DIMS = {d: 5.0 for d in DIMENSIONS}


@pytest.mark.asyncio
async def test_enabled_with_two_spikes_blocks_with_the_reason():
    result = await _evaluate(_with_rule(max_spikes=0, max_suite_regressions=1), GO_DIMS, 99.5,
                             {"duration_regressions": TWO_SPIKES})
    assert result.recommendation == "NO_GO"
    assert result.overall_result == "BLOCK"
    [ev] = result.rule_evaluations
    assert ev.rule_type == "perf_regression" and ev.passed is False
    assert ev.message.startswith("2 tests slower than baseline and 1 suite slower than its history")
    assert ev.actual_value == 3


@pytest.mark.asyncio
async def test_the_same_policy_without_findings_is_a_go():
    result = await _evaluate(_with_rule(), GO_DIMS, 99.5, {})
    assert result.recommendation == "GO"
    [ev] = result.rule_evaluations
    assert ev.passed is True
    assert ev.message == "Duration regressions were not measured for this run (not evaluated) — rule not applied"


@pytest.mark.asyncio
async def test_an_unmeasured_run_never_blocks_and_names_the_reason():
    findings = {"measured": False, "reason": "not enough history (4 of 10)", "spikes": [], "suites": []}
    result = await _evaluate(_with_rule(), GO_DIMS, 99.5, {"duration_regressions": findings})
    assert result.recommendation == "GO"
    assert "not enough history (4 of 10)" in result.rule_evaluations[0].message


@pytest.mark.asyncio
async def test_a_disabled_rule_is_not_evaluated():
    doc = _with_rule()
    doc["rules"][0]["enabled"] = False
    result = await _evaluate(doc, GO_DIMS, 99.5, {"duration_regressions": TWO_SPIKES})
    assert result.recommendation == "GO" and result.rule_evaluations == []


@pytest.mark.asyncio
async def test_warn_turns_go_into_conditional_go_only():
    result = await _evaluate(_with_rule(action="WARN"), GO_DIMS, 99.5, {"duration_regressions": TWO_SPIKES})
    assert result.recommendation == "CONDITIONAL_GO"
    assert result.overall_result == "WARN"


@pytest.mark.asyncio
async def test_min_sigma_and_suite_ratio_tighten_what_counts():
    tight = _with_rule(max_spikes=1, min_sigma=4.0, suite_ratio=2.0)
    result = await _evaluate(tight, GO_DIMS, 99.5, {"duration_regressions": TWO_SPIKES})
    assert result.recommendation == "GO"
    assert result.rule_evaluations[0].message.startswith("1 test slower than baseline")
    over = await _evaluate(_with_rule(max_spikes=1, max_suite_regressions=1), GO_DIMS, 99.5,
                           {"duration_regressions": TWO_SPIKES})
    assert over.recommendation == "NO_GO"


@pytest.mark.asyncio
async def test_the_uncapped_count_is_used_when_the_stored_list_was_capped():
    findings = {**TWO_SPIKES, "spike_count": 75, "suites": [], "suite_regression_count": 0}
    result = await _evaluate(_with_rule(max_spikes=10), GO_DIMS, 99.5, {"duration_regressions": findings})
    assert result.recommendation == "NO_GO"
    assert result.rule_evaluations[0].message.startswith("75 tests slower than baseline")


def test_the_policy_document_accepts_the_rule():
    from app.models.schemas import PolicyDocument

    doc = PolicyDocument.model_validate(_with_rule(max_spikes=2, min_sigma=3.5, action="BLOCK"))
    assert doc.rules[0].type == "perf_regression"
    assert doc.rules[0].params["max_spikes"] == 2


EDITOR_DEFAULTS = {"max_spikes": 0, "min_sigma": 3, "suite_ratio": 1.5, "max_suite_regressions": 0,
                   "action": "BLOCK"}


def test_save_accepts_the_editors_rule_and_refuses_bad_params_by_name():
    from fastapi import HTTPException

    from app.models.schemas import PolicyDocument
    from app.routers.release_gate_policies import _validate_policy_document

    _validate_policy_document(PolicyDocument.model_validate(_with_rule(**EDITOR_DEFAULTS)))
    for bad, field in (
        ({"max_spikes": -1}, "max_spikes"),
        ({"max_spikes": 1.5}, "max_spikes"),
        ({"min_sigma": "high"}, "min_sigma"),
        ({"suite_ratio": 0.5}, "suite_ratio"),
        ({"action": "PANIC"}, "action"),
    ):
        with pytest.raises(HTTPException) as exc:
            _validate_policy_document(PolicyDocument.model_validate(_with_rule(**{**EDITOR_DEFAULTS, **bad})))
        assert exc.value.status_code == 422
        assert exc.value.detail.startswith(f"perf_regression: {field}")


def test_every_rule_type_the_editor_offers_is_one_the_api_saves_and_evaluates():
    """The editor's RULE_TYPES and the API's accepted types are two lists in
    two languages; a type only the editor knows saves with a silent 422."""
    import re
    from pathlib import Path

    from fastapi import HTTPException

    from app.models.schemas import PolicyDocument
    from app.routers.release_gate_policies import _validate_policy_document

    editor = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "PolicyEditorPage.tsx")
    source = editor.read_text(encoding="utf-8")
    block = source[source.index("const RULE_TYPES = ["):]
    block = block[: block.index("];")]
    offered = re.findall(r"type: '([a-z_]+)'", block)
    assert "perf_regression" in offered and len(offered) >= 5
    for rule_type in offered:
        doc = _with_rule(**EDITOR_DEFAULTS)
        doc["rules"][0]["type"] = rule_type
        if rule_type == "dimension_ceiling":
            doc["rules"][0]["params"] = {"dimension": "regression_likely", "max_score": 70}
        try:
            _validate_policy_document(PolicyDocument.model_validate(doc))
        except HTTPException as exc:  # pragma: no cover - the failure message is the point
            pytest.fail(f"the editor offers {rule_type!r} and the API refuses it: {exc.detail}")
        assert rule_type == "override_rules" or rule_type in pe._RULE_EVALUATORS, rule_type


# ── The release risk agent feeds the rule ───────────────────────────────────


class _Session:
    def __init__(self, findings):
        self.findings = findings

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.findings)


@pytest.mark.asyncio
async def test_the_agent_attaches_stored_findings_and_nothing_otherwise():
    from app.agents import release_risk_agent as agent_module
    from app.agents.release_risk_agent import ReleaseRiskAgent

    state = {"test_run_id": str(uuid.uuid4())}
    stored = {"measured": True, "spike_count": 1, "spikes": [{"test_name": "a", "sigma": 4.0}],
              "suites": [], "suite_regression_count": 0}
    context = {"flaky_count": 0}
    with patch.object(agent_module, "AsyncSessionLocal", lambda: _Session(stored)):
        await ReleaseRiskAgent._attach_duration_regressions(state, context)
    assert context["duration_regressions"]["spike_count"] == 1

    for findings in (None,):
        untouched = {"flaky_count": 0}
        with patch.object(agent_module, "AsyncSessionLocal", lambda: _Session(findings)):
            await ReleaseRiskAgent._attach_duration_regressions(state, untouched)
        assert untouched == {"flaky_count": 0}

    broken = {"flaky_count": 0}

    def boom():
        raise RuntimeError("db down")

    with patch.object(agent_module, "AsyncSessionLocal", boom):
        await ReleaseRiskAgent._attach_duration_regressions(state, broken)
    assert broken == {"flaky_count": 0}


@pytest.mark.asyncio
async def test_the_simulator_reads_the_runs_stored_findings():
    from app.routers import release_gate_policies as router

    run = SimpleNamespace(pass_rate=99.5, duration_findings={**TWO_SPIKES, "spikes": TWO_SPIKES["spikes"]})
    decision = SimpleNamespace(dimension_scores=GO_DIMS, input_snapshot={}, recommendation="GO",
                               risk_score=5.0)
    project_id = uuid.uuid4()
    results = iter([
        SimpleNamespace(scalar_one_or_none=lambda: project_id),
        SimpleNamespace(scalar_one_or_none=lambda: decision),
        SimpleNamespace(scalar_one_or_none=lambda: run),
    ])
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=lambda *_a, **_k: next(results))
    captured = {}
    real = pe.evaluate_policy

    async def spy(**kwargs):
        captured.update(kwargs["context"])
        return await real(**kwargs)

    payload = SimpleNamespace(run_id=uuid.uuid4(), policy_document=SimpleNamespace(
        model_dump=lambda: _with_rule()))
    with patch.object(pe, "evaluate_policy", spy), \
            patch.object(router, "_enforce_api_key_project_binding", lambda *a, **k: None), \
            patch("app.core.deps.resolve_project_scope", AsyncMock()):
        try:
            await router.simulate_policy(payload=payload, db=db, current_user=SimpleNamespace(id=uuid.uuid4()))
        except Exception:  # noqa: BLE001 -- only the context handed to the evaluator matters here
            pass
    assert captured.get("duration_regressions", {}).get("spike_count") == 2
