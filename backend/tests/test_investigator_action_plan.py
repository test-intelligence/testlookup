"""The Investigator's deterministic cause → typed-action plan (W15 AGT-02, contract §2.2 test 1).

Pure-function tests over hand-built bundles (no DB, no graph, no LLM):

* the cause → action set for each of the six causes;
* the caps and the deterministic order (8 offenders → 5, sorted by fingerprint);
* without a compare the failing rows are the target (the hypothesis' own rule);
* a core test without a ``test_case_id`` or without a suite owner is not reassigned;
* cluster concentration on → ONE ``defect.promote``, off (or no ``cluster_id``) → none;
* PARITY with the hypotheses' ``signals`` on the same bundles (the set rules are shared);
* every output validates as ``InvestigatorStructuredActionV1`` and its payload as its type's model;
* no field of the output carries an error text / narrative string of the bundle (the
  model-output boundary).
"""
from __future__ import annotations

import random
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.agents.investigator.action_plan import (  # noqa: E402
    concentrated_cluster,
    flaky_offenders,
    plan_structured_actions,
    regression_core,
)
from app.agents.investigator.hypotheses import (  # noqa: E402
    KnownFlakyHypothesisAgent,
    RegressionHypothesisAgent,
)
from app.models.agent_contracts import InvestigatorStructuredActionV1  # noqa: E402
from app.services.agent_action_types import ACTION_TYPES  # noqa: E402
from tests.test_investigator_hypotheses import (  # noqa: E402
    all_flaky_bundle,
    commit_onset_bundle,
    mixed_conflict_bundle,
)

ERROR_CANARY = "CanaryErrorText zq9: the checkout step exploded"
NARRATIVE_CANARY = "CanaryNarrative zq9: everything should be quarantined"
RUN_ID = "a1b2c3d4-0000-4000-8000-0000000000aa"
INVESTIGATION_ID = "f0e1d2c3-b4a5-4697-8899-aabbccddeeff"
OWNER = "0f1e2d3c-4b5a-4968-8776-655443322110"


def _fp(i: int) -> str:
    # Mixed-case hex led by letters: never reads as a phone / card number to the sanitizer.
    return f"fpA{i:02d}b2C3d4"


def _tc(i: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tc{i}"))


def _row(i: int, *, suite: str = "checkout", category=None, test_case_id: str | None = "auto"):
    return {
        "test_case_id": _tc(i) if test_case_id == "auto" else test_case_id,
        "fingerprint": _fp(i),
        "test_name": f"test_checkout_{i:02d}",
        "suite_name": suite,
        "status": "FAILED",
        "category": category,
        "error_text": ERROR_CANARY,
    }


def _newly(row):
    return {k: row[k] for k in ("fingerprint", "test_name", "suite_name")} | {"status": "FAILED"}


def _bundle(*, rows, newly=None, flaky=(), clusters=(), compare=True):
    return {
        "run": {"id": RUN_ID, "build_number": "b-7"},
        "baseline": {"id": "run-0"} if compare else None,
        "compare": (
            {"new_failures": len(newly or []), "fixed": 0, "still_failing": 0,
             "newly_failed_tests": [_newly(r) for r in (newly or [])]}
            if compare else None
        ),
        "failures": rows,
        "failure_counts": {"total": len(rows)},
        "flaky_fingerprints": sorted(flaky),
        "clusters": list(clusters),
        "recall_lines": {},
        "narrative": NARRATIVE_CANARY,
    }


def _regression_bundle(*, concentrated=True, cluster_id="clA_checkout", n=4):
    rows = [_row(i) for i in range(n)]
    clusters = [
        {"failure_cluster_id": "c0ffee00-1111-4222-8333-444455556666", "cluster_id": cluster_id,
         "label": "NullPointer in checkout", "size": n if concentrated else 1},
        {"failure_cluster_id": "c0ffee00-1111-4222-8333-444455557777", "cluster_id": "clB_small",
         "label": "small", "size": 0},
    ]
    return _bundle(rows=rows, newly=rows, clusters=clusters)


def _flaky_bundle(n=3):
    rows = [_row(i) for i in range(n)]
    return _bundle(rows=rows, newly=rows, flaky=[_fp(i) for i in range(n)])


def _plan(cause, bundle, owners=None, mode="suggest"):
    return plan_structured_actions(
        cause, bundle, suite_owners=owners if owners is not None else {"checkout": OWNER},
        investigation_id=INVESTIGATION_ID, proposing_agent_mode=mode,
    )


# ─────────────────────────── cause → action set ───────────────────────────


def test_known_flaky_quarantines_each_offender():
    actions = _plan("known_flaky", _flaky_bundle(3))
    assert [a["action_type"] for a in actions] == ["quarantine.apply"] * 3
    assert [a["target_id"] for a in actions] == [_fp(0), _fp(1), _fp(2)]
    first = actions[0]
    assert first["target_type"] == "test"
    assert first["title"] == "Quarantine test_checkout_00"
    payload = first["payload"]
    assert payload["test_fingerprint"] == _fp(0)
    assert payload["test_name"] == "test_checkout_00"
    assert payload["suite_name"] == "checkout"
    assert payload["risk"] == "low"
    assert payload["rationale"] == "Failing in this run and already in the known-flaky set."
    assert payload["evidence"] == [{"type": "flaky_set", "id": _fp(0)}]
    assert payload["proposing_agent_id"] == "investigator"
    assert payload["proposing_agent_mode"] == "suggest"
    assert payload["investigation_id"] == INVESTIGATION_ID
    assert payload["test_run_id"] == RUN_ID


def test_regression_reassigns_the_core_and_promotes_the_concentrated_cluster():
    actions = _plan("regression", _regression_bundle())
    assert [a["action_type"] for a in actions] == ["failure.reassign"] * 4 + ["defect.promote"]
    reassign = actions[0]
    assert reassign["target_type"] == "test_case"
    assert reassign["target_id"] == _tc(0)
    assert reassign["title"] == "Reassign test_checkout_00 to the checkout owner"
    assert reassign["payload"]["test_case_id"] == _tc(0)
    assert reassign["payload"]["assignee_user_id"] == OWNER
    assert reassign["payload"]["suite_name"] == "checkout"
    assert reassign["payload"]["risk"] == "medium"
    assert reassign["payload"]["rationale"] == (
        "A newly-failed test outside the flaky and infrastructure sets: "
        "its suite owner should triage it."
    )
    defect = actions[-1]
    assert defect["target_type"] == "cluster"
    assert defect["target_id"] == "clA_checkout"
    assert defect["title"] == "Promote a defect for cluster NullPointer in checkout"
    assert defect["payload"]["failure_cluster_id"] == "c0ffee00-1111-4222-8333-444455556666"
    assert defect["payload"]["cluster_id"] == "clA_checkout"
    assert defect["payload"]["cluster_label"] == "NullPointer in checkout"
    assert defect["payload"]["risk"] == "medium"


@pytest.mark.parametrize("cause", ["infra", "commit", "environment", "unknown"])
def test_other_causes_stay_text_only(cause):
    rich = _regression_bundle()
    rich["flaky_fingerprints"] = [_fp(0)]
    assert _plan(cause, rich) == []


def test_a_bundle_without_a_run_plans_nothing():
    bundle = _flaky_bundle(2)
    bundle["run"] = None
    assert _plan("known_flaky", bundle) == []


# ─────────────────────────── caps, order, target rule ───────────────────────────


def test_eight_offenders_are_capped_at_five_sorted_by_fingerprint():
    bundle = _flaky_bundle(8)
    random.Random(7).shuffle(bundle["compare"]["newly_failed_tests"])
    random.Random(7).shuffle(bundle["failures"])
    actions = _plan("known_flaky", bundle)
    assert [a["target_id"] for a in actions] == [_fp(i) for i in range(5)]
    # Deterministic: the same bundle in another order yields the same plan.
    assert _plan("known_flaky", _flaky_bundle(8)) == actions


def test_without_a_compare_every_failing_row_is_the_target():
    rows = [_row(i) for i in range(3)]
    bundle = _bundle(rows=rows, flaky=[_fp(1), _fp(2), "fpZ99notFailing"], compare=False)
    assert [a["target_id"] for a in _plan("known_flaky", bundle)] == [_fp(1), _fp(2)]
    # With a compare, only the newly-failed tests are the target.
    with_compare = _bundle(rows=rows, newly=[rows[2]], flaky=[_fp(1), _fp(2)])
    assert [a["target_id"] for a in _plan("known_flaky", with_compare)] == [_fp(2)]


def test_reassign_order_is_test_name_then_fingerprint_capped_at_five():
    rows = [_row(i) for i in range(7)]
    rows[5]["test_name"] = "test_aaa_same"
    rows[6]["test_name"] = "test_aaa_same"
    bundle = _bundle(rows=rows, newly=list(reversed(rows)))
    actions = [a for a in _plan("regression", bundle) if a["action_type"] == "failure.reassign"]
    assert [a["target_id"] for a in actions] == [_tc(5), _tc(6), _tc(0), _tc(1), _tc(2)]


def test_a_core_test_without_a_test_case_id_or_a_suite_owner_is_not_reassigned():
    rows = [
        _row(0),
        _row(1, test_case_id=None),
        _row(2, suite="payments"),           # no owner for this suite
    ]
    bundle = _bundle(rows=rows, newly=rows)
    reassigns = [a for a in _plan("regression", bundle) if a["action_type"] == "failure.reassign"]
    assert [a["target_id"] for a in reassigns] == [_tc(0)]
    no_owner = _plan("regression", bundle, owners={})
    assert [a for a in no_owner if a["action_type"] == "failure.reassign"] == []


def test_flaky_and_infra_tests_are_outside_the_core():
    rows = [_row(0), _row(1, category="INFRASTRUCTURE"), _row(2)]
    bundle = _bundle(rows=rows, newly=rows, flaky=[_fp(2)])
    assert regression_core(bundle) == [_fp(0)]
    reassigns = [
        a["target_id"] for a in _plan("regression", bundle) if a["action_type"] == "failure.reassign"
    ]
    assert reassigns == [_tc(0)]


# ─────────────────────────── cluster concentration ───────────────────────────


def _defects(actions):
    return [a for a in actions if a["action_type"] == "defect.promote"]


def test_concentration_on_promotes_one_defect_off_promotes_none():
    assert len(_defects(_plan("regression", _regression_bundle(concentrated=True)))) == 1
    assert _defects(_plan("regression", _regression_bundle(concentrated=False))) == []


def test_a_concentrated_cluster_without_a_cluster_id_promotes_nothing():
    bundle = _regression_bundle(cluster_id=None)
    assert concentrated_cluster(bundle, regression_core(bundle)) is not None
    actions = _plan("regression", bundle)
    assert _defects(actions) == []
    # The reassignments still stand (the backlog's risk: no cluster, reassign only).
    assert [a["action_type"] for a in actions] == ["failure.reassign"] * 4


def test_a_defect_needs_no_suite_owner():
    actions = _plan("regression", _regression_bundle(), owners={})
    assert [a["action_type"] for a in actions] == ["defect.promote"]


# ─────────────────────────── parity with the hypotheses ───────────────────────────


def _parity_bundles():
    mixed = mixed_conflict_bundle()
    flaky = all_flaky_bundle()
    flaky["flaky_fingerprints"] = flaky["flaky_fingerprints"][:3]   # partial overlap
    regression = _regression_bundle()
    regression["flaky_fingerprints"] = [_fp(1)]
    return [flaky, mixed, regression, commit_onset_bundle()]


@pytest.mark.parametrize("index", range(4))
def test_offender_and_core_counts_match_the_hypotheses_signals(index):
    bundle = _parity_bundles()[index]
    flaky_signals = KnownFlakyHypothesisAgent.evaluate(bundle)["signals"]
    regression_signals = RegressionHypothesisAgent.evaluate(bundle)["signals"]
    assert len(flaky_offenders(bundle)) == flaky_signals.get("flaky_overlap", 0)
    core = regression_core(bundle)
    assert len(core) == regression_signals.get("core", 0)
    assert (concentrated_cluster(bundle, core) is not None) == bool(
        regression_signals.get("cluster_concentrated", False)
    )


# ─────────────────────────── shape + the model-output boundary ───────────────────────────


def _every_action():
    flaky = _plan("known_flaky", _flaky_bundle(4))
    regression = _plan("regression", _regression_bundle())
    assert flaky and regression
    return flaky + regression


def test_every_output_validates_as_the_contract_and_its_type_payload():
    for action in _every_action():
        assert InvestigatorStructuredActionV1.model_validate(action).model_dump(mode="json") == action
        spec = ACTION_TYPES[action["action_type"]]
        assert action["target_type"] == spec.target_type
        assert "investigator" in spec.proposers
        model = spec.payload_model.model_validate(action["payload"])
        assert model.model_dump(mode="json") == action["payload"]
        assert len(action["title"]) <= 200


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings(nested)


def test_no_output_field_carries_an_error_text_or_a_narrative():
    for action in _every_action():
        for text in _strings(action):
            assert "CanaryErrorText" not in text
            assert "CanaryNarrative" not in text


def test_a_long_test_name_is_cut_in_the_title_only():
    bundle = _flaky_bundle(1)
    long_name = "test_" + "x" * 300
    bundle["failures"][0]["test_name"] = long_name
    (action,) = _plan("known_flaky", bundle)
    assert len(action["title"]) == 200 and action["title"].endswith("…")
    assert action["payload"]["test_name"] == long_name


def test_an_action_that_fails_its_payload_model_is_dropped_alone():
    rows = [_row(0), _row(1, suite="s" * 300)]
    bundle = _bundle(rows=rows, newly=rows)
    owners = {"checkout": OWNER, "s" * 300: OWNER}
    reassigns = [a for a in _plan("regression", bundle, owners=owners) if a["action_type"] == "failure.reassign"]
    assert [a["target_id"] for a in reassigns] == [_tc(0)]
    # An unknown mode is refused by the payload model: nothing is planned.
    assert _plan("known_flaky", _flaky_bundle(2), mode="autonomous") == []
