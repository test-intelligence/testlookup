"""Phase 4: the verdict must be honest, complete, and powerless to hide.

Roadmap Phase 4 (``architecture/TEST_INTELLIGENCE_PLAN.md``) — the flagship.

Three properties carry this phase, and every test pins one:

**1. UNCERTAIN when the signals disagree.** At Google ~84% of pass→fail
transitions involve a flaky test, so a raw transition is weak evidence. A
surface that resolves conflicting signals to whichever looked strongest would
manufacture exactly the false confidence the verdict exists to remove.

**2. Every input is shown.** Practitioners reject generic factor-level
explanations and want the specific changed files and prior failures named. A
verdict whose evidence is invisible is one users are asked to take on faith.

**3. No verdict can suppress anything.** Roughly 1 in 6 newly-flaky tests
reflected a real production bug, so ``LIKELY_FLAKY`` must never read as "safe to
ignore" — and no code path may convert a verdict into silence.
"""
from __future__ import annotations

import inspect

import pytest

from app.services.failure_attribution_service import (
    CHANGE_OVERLAP_FLOOR,
    FLAKY_SCORE_FLOOR,
    VERDICT_DESCRIPTIONS,
    VERDICT_LABELS,
    Attribution,
    AttributionInputs,
    AttributionVerdict,
    compose,
)


def _inputs(**overrides) -> AttributionInputs:
    """A newly-failing test on a project whose classifier measured well."""
    base = {"is_new_failure": True, "calibration_mode": "advisory"}
    base.update(overrides)
    return AttributionInputs(**base)


# ── 1. Disagreement and thin evidence both yield UNCERTAIN ───────────────────

def test_conflicting_signals_yield_uncertain_not_the_strongest_one():
    """THE Phase 4 guard. A cluster says infra, the diff says your change.
    Resolving that to whichever scored higher would invent a confidence the
    evidence does not support."""
    result = compose(_inputs(
        cluster_key="sfc_001", cluster_size=5, cluster_cause_family="networking",
        change_overlap=0.9, changed_files=("src/checkout.py",),
    ))
    assert result.verdict is AttributionVerdict.UNCERTAIN
    assert "disagree" in result.rationale.lower()
    assert result.confidence == 0.0


def test_no_signal_at_all_yields_uncertain_with_a_reason():
    result = compose(_inputs())
    assert result.verdict is AttributionVerdict.UNCERTAIN
    assert result.rationale


def test_an_already_failing_test_is_not_attributed_to_whoever_pushed_next():
    """A test that was already red has no transition to attribute. Saying so
    beats blaming the next person to push."""
    result = compose(_inputs(is_new_failure=False, change_overlap=0.95))
    assert result.verdict is AttributionVerdict.UNCERTAIN
    assert "not a new failure" in result.rationale.lower()


# ── Each signal, alone, reaches its verdict ──────────────────────────────────

def test_cluster_membership_alone_says_infrastructure():
    """Co-failure across tests sharing no code path is the most checkable
    claim available — you can go look at the dependency."""
    result = compose(_inputs(
        cluster_key="sfc_002", cluster_size=14, cluster_cause_family="external_dependency",
    ))
    assert result.verdict is AttributionVerdict.LIKELY_INFRA
    assert "13 other" in result.rationale
    assert "external dependency" in result.rationale


def test_change_overlap_alone_says_your_change_and_names_the_files():
    result = compose(_inputs(
        change_overlap=0.8,
        changed_files=("src/checkout/total.py", "src/checkout/tax.py"),
    ))
    assert result.verdict is AttributionVerdict.LIKELY_YOUR_CHANGE
    assert "src/checkout/total.py" in result.rationale


def test_flaky_history_alone_says_flaky_but_never_says_ignore():
    result = compose(_inputs(flaky_score=0.8, flaky_confidence="high"))
    assert result.verdict is AttributionVerdict.LIKELY_FLAKY
    lowered = result.rationale.lower()
    assert "worth a look" in lowered or "real bug" in lowered
    for forbidden in ("safe to ignore", "can be ignored", "no action"):
        assert forbidden not in lowered


def test_a_single_member_cluster_is_not_evidence_of_anything():
    result = compose(_inputs(cluster_key="sfc_003", cluster_size=1))
    assert result.verdict is AttributionVerdict.UNCERTAIN


# ── Thin evidence must not vote ──────────────────────────────────────────────

@pytest.mark.parametrize("confidence", ["none", "low"])
def test_a_thin_history_flaky_score_does_not_vote(confidence):
    """Phase 2 refuses to emit a score it cannot support; that refusal must not
    be undone here by letting a weak score cast a full-strength vote."""
    result = compose(_inputs(flaky_score=0.95, flaky_confidence=confidence))
    assert result.verdict is AttributionVerdict.UNCERTAIN


def test_weak_overlap_does_not_vote():
    result = compose(_inputs(change_overlap=CHANGE_OVERLAP_FLOOR - 0.01))
    assert result.verdict is AttributionVerdict.UNCERTAIN


def test_low_flaky_score_does_not_vote():
    result = compose(_inputs(
        flaky_score=FLAKY_SCORE_FLOOR - 0.01, flaky_confidence="high"
    ))
    assert result.verdict is AttributionVerdict.UNCERTAIN


# ── Calibration caps the claim ───────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["hint", "unmeasured", ""])
def test_a_weak_classifier_caps_the_verdict_at_uncertain(mode):
    """Measured specificity swings from 100% to no-better-than-random across
    projects. Where it measures weak — or was never measured — a conclusion is
    borrowing authority it has not earned."""
    result = compose(_inputs(
        calibration_mode=mode,
        cluster_key="sfc_004", cluster_size=9, cluster_cause_family="networking",
    ))
    assert result.verdict is AttributionVerdict.UNCERTAIN
    assert "classifier" in result.rationale.lower()
    # The evidence is still reported — capped, not hidden.
    assert result.inputs.cluster_key == "sfc_004"


# ── 2. Every input is shown ──────────────────────────────────────────────────

# The five signals the plan requires. Guarding the SET means adding a signal
# without exposing it fails here.
REQUIRED_INPUT_FIELDS = {
    "is_new_failure",
    "flaky_score",
    "cluster_key",
    "change_overlap",
    "calibration_mode",
}


def test_the_payload_carries_every_composed_signal():
    payload = compose(_inputs(
        flaky_score=0.4, flaky_confidence="medium",
        cluster_key="sfc_005", cluster_size=3,
        change_overlap=0.2, changed_files=("a.py",),
        calibration_specificity=0.97,
    )).to_dict()
    missing = REQUIRED_INPUT_FIELDS - set(payload["inputs"])
    assert not missing, f"verdict payload hides its inputs: {sorted(missing)}"


def test_the_payload_shows_the_votes_behind_the_verdict():
    """Which signals argued for what, not just the outcome."""
    payload = compose(_inputs(cluster_key="sfc_006", cluster_size=4)).to_dict()
    assert payload["votes"]


# ── The enum vocabulary, guarded as a CLASS ──────────────────────────────────

def test_every_verdict_member_has_a_label_and_a_description():
    """The vocabulary-subset defect class behind F-074, F-078 and UAT-002: a
    producer grows a state and a consumer silently keeps rendering the old set.
    Iterate the enum so a new member cannot be added without its copy."""
    for verdict in AttributionVerdict:
        assert verdict in VERDICT_LABELS, f"{verdict.value} has no label"
        assert verdict in VERDICT_DESCRIPTIONS, f"{verdict.value} has no description"
        assert VERDICT_LABELS[verdict].strip()
        assert VERDICT_DESCRIPTIONS[verdict].strip()


def test_no_label_map_carries_a_member_the_enum_does_not():
    members = set(AttributionVerdict)
    assert set(VERDICT_LABELS) == members
    assert set(VERDICT_DESCRIPTIONS) == members


def test_every_verdict_is_reachable_by_some_input_combination():
    """A verdict nobody can produce is dead vocabulary that will drift."""
    reached = {
        compose(_inputs()).verdict,
        compose(_inputs(cluster_key="c", cluster_size=3)).verdict,
        compose(_inputs(change_overlap=0.9, changed_files=("a.py",))).verdict,
        compose(_inputs(flaky_score=0.9, flaky_confidence="high")).verdict,
    }
    assert reached == set(AttributionVerdict)


# ── 3. Nothing may suppress ──────────────────────────────────────────────────

def test_the_service_offers_no_way_to_suppress_a_failure():
    """No code path may convert a verdict into silence. ~1 in 6 newly-flaky
    tests reflected a real production bug."""
    import app.services.failure_attribution_service as module

    source = inspect.getsource(module)
    for forbidden in ("suppress", "auto_close", "hide_failure", "silence"):
        # The word may appear in prose explaining why we do NOT do it; it must
        # never appear as an attribute or key the code sets.
        assert f'"{forbidden}"' not in source, f"a {forbidden} field would be a suppression hook"
        assert f"{forbidden}=" not in source


def test_every_payload_restates_that_the_verdict_is_advisory():
    for result in (
        compose(_inputs(flaky_score=0.99, flaky_confidence="high")),
        compose(_inputs()),
    ):
        assert "no verdict suppresses" in result.to_dict()["policy"].lower()


def test_the_persisted_row_has_no_suppression_column():
    """Guard the SCHEMA, not just the service. A column is an invitation."""
    from app.models.postgres import FailureAttribution

    columns = set(FailureAttribution.__table__.columns.keys())
    for forbidden in ("suppressed", "hidden", "muted", "ignored", "auto_closed"):
        assert forbidden not in columns


def test_compose_never_raises_on_malformed_signals():
    result = compose(AttributionInputs(
        is_new_failure=True,
        flaky_score="not a number",       # type: ignore[arg-type]
        change_overlap=float("nan"),
        cluster_size=-5,
        calibration_mode="advisory",
    ))
    assert isinstance(result, Attribution)
    assert result.verdict in set(AttributionVerdict)


# ── Review finding: signals must read real columns ───────────────────────────

def test_every_orm_attribute_the_composer_reads_actually_exists():
    """Guard the CLASS. This is the second time a signal has been wired to a
    column that does not exist: Phase 0's OpenShift branch read
    ``oc_namespace`` when the column is ``ocp_namespace``, and this module
    first read ``run.changed_files`` when the changed files live in
    ``run_commit_ranges.commits[].files``.

    Both failed SILENTLY — ``getattr(..., default)`` swallows the typo and the
    signal simply never votes. Nothing raises, no test fails, and the feature
    is quietly dead. Assert the attributes exist on the real models.
    """
    from app.models.postgres import (
        FlakyClassifierCalibration,
        FlakyScore,
        RunCommitRange,
        SystemicFlakeCluster,
        SystemicFlakeClusterMember,
        TestCase,
        TestRun,
    )

    expected = {
        TestRun: ("project_id", "created_at"),
        TestCase: ("test_run_id", "test_fingerprint", "status"),
        FlakyScore: ("project_id", "test_fingerprint", "score", "confidence"),
        SystemicFlakeCluster: ("project_id", "cluster_key", "cause_family", "size"),
        SystemicFlakeClusterMember: ("cluster_id", "test_fingerprint"),
        FlakyClassifierCalibration: ("project_id", "specificity", "sample_count"),
        RunCommitRange: ("run_id", "commits"),
    }
    for model, attributes in expected.items():
        for attribute in attributes:
            assert hasattr(model, attribute), (
                f"attribute_run reads {model.__name__}.{attribute}, which no longer exists"
            )


def test_changed_files_come_from_the_commit_range_not_the_run():
    """TestRun has no ``changed_files`` column; reading one would make the
    LIKELY_YOUR_CHANGE signal permanently dead."""
    import app.services.failure_attribution_service as module

    source = inspect.getsource(module.attribute_run)
    assert "RunCommitRange" in source
    assert 'getattr(run, "changed_files"' not in source


def test_an_unknown_commit_range_does_not_vote_against_the_change():
    """No commits known is an ABSENCE of overlap evidence, not evidence of no
    overlap. It must leave the verdict uncertain, not exonerate the change."""
    result = compose(_inputs(change_overlap=None, changed_files=()))
    assert result.verdict is AttributionVerdict.UNCERTAIN
