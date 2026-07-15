"""
AI-F1 — ML label integrity: provenance taxonomy, composition policy,
metadata honesty, and the pseudo-heavy vs human-weighted comparison harness.

The learning loop previously trained mostly on the LLM's own high-confidence
outputs (pseudo-labels), so retraining taught the classifier to imitate the
LLM. These tests pin the fix:

  - every training example is bucketed as human_direct / human_indirect /
    llm_pseudo, derived from existing fields (no schema change);
  - pseudo-labels are capped (ML_PSEUDO_LABEL_CAP) and down-weighted
    (ML_PSEUDO_LABEL_WEIGHT); human labels always ride at full weight;
  - the deployed model's metadata records the mix, and the routing decision
    record reports "bootstrap_llm_imitating" below ML_HUMAN_LABEL_FLOOR;
  - a regression harness shows the human-weighted policy beats the old
    pseudo-heavy regime on a human-labeled holdout when pseudo-labels are
    systematically biased.
"""
import json

import pytest

pytest.importorskip("asyncpg")

from app.services.ml.label_provenance import (  # noqa: E402
    HUMAN_DIRECT,
    HUMAN_INDIRECT,
    LLM_PSEUDO,
    MATURITY_BOOTSTRAP,
    MATURITY_HUMAN_CALIBRATED,
    MATURITY_NOT_TRAINED,
    apply_composition_policy,
    model_maturity_from_metadata,
    provenance_for_feedback_source,
    resolve_feedback_label,
)
from app.services.ml.feature_extractor import FEATURE_NAMES  # noqa: E402


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Provenance derivation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestProvenanceForFeedbackSource:
    def test_manual_is_human_direct(self):
        assert provenance_for_feedback_source("manual") == HUMAN_DIRECT

    def test_category_correction_is_human_direct(self):
        assert provenance_for_feedback_source("category_correction") == HUMAN_DIRECT

    def test_jira_resolved_is_human_indirect(self):
        assert provenance_for_feedback_source("jira_resolved") == HUMAN_INDIRECT

    def test_jira_invalid_is_human_indirect(self):
        assert provenance_for_feedback_source("jira_invalid") == HUMAN_INDIRECT

    def test_unknown_source_defaults_to_human_indirect(self):
        """Any ai_feedback row is human-originated — an unknown source must
        never be mistaken for an LLM pseudo-label."""
        assert provenance_for_feedback_source("future_source") == HUMAN_INDIRECT
        assert provenance_for_feedback_source(None) == HUMAN_INDIRECT


class TestResolveFeedbackLabel:
    def test_correct_rating_confirms_analysis_category(self):
        assert resolve_feedback_label("correct", None, "INFRASTRUCTURE") == "INFRASTRUCTURE"

    def test_incorrect_with_correction_uses_corrected_category(self):
        assert resolve_feedback_label("incorrect", "PRODUCT_BUG", "FLAKY") == "PRODUCT_BUG"

    def test_incorrect_without_correction_is_unusable(self):
        # "It's wrong" without "here's what's right" is not a training label.
        assert resolve_feedback_label("incorrect", None, "FLAKY") is None

    def test_partially_correct_is_unusable(self):
        assert resolve_feedback_label("partially_correct", "PRODUCT_BUG", "FLAKY") is None

    def test_correct_with_null_analysis_category_is_unusable(self):
        assert resolve_feedback_label("correct", None, None) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Composition policy matrix
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _pool(n_human_direct: int, n_human_indirect: int, n_pseudo: int):
    """Build an aligned (samples, labels, provenances) pool."""
    samples, labels, provenances = [], [], []
    for kind, count in (
        (HUMAN_DIRECT, n_human_direct),
        (HUMAN_INDIRECT, n_human_indirect),
        (LLM_PSEUDO, n_pseudo),
    ):
        for i in range(count):
            samples.append({"i": i, "kind": kind})
            labels.append("PRODUCT_BUG" if i % 2 else "INFRASTRUCTURE")
            provenances.append(kind)
    return samples, labels, provenances


_POLICY = dict(pseudo_cap=0.30, pseudo_weight=0.3, min_samples=200, human_label_floor=50)


class TestCompositionPolicy:
    def test_cap_enforced_when_humans_plentiful(self):
        samples, labels, prov = _pool(500, 200, 1000)
        out_s, out_l, out_p, weights, comp = apply_composition_policy(
            samples, labels, prov, **_POLICY,
        )
        n_pseudo = sum(1 for p in out_p if p == LLM_PSEUDO)
        # pseudo/(human+pseudo) must be <= 30%
        assert n_pseudo / len(out_s) <= 0.30 + 1e-9
        assert comp["pseudo_included"] == n_pseudo
        assert comp["pseudo_dropped"] == 1000 - n_pseudo
        assert comp["cap_exceeded_to_fill_floor"] is False
        # All 700 human labels kept
        assert comp["human_label_count"] == 700
        assert sum(1 for p in out_p if p != LLM_PSEUDO) == 700

    def test_pseudo_downweighted_humans_full_weight(self):
        samples, labels, prov = _pool(100, 50, 500)
        _, _, out_p, weights, _ = apply_composition_policy(
            samples, labels, prov, **_POLICY,
        )
        for p, w in zip(out_p, weights):
            assert w == (0.3 if p == LLM_PSEUDO else 1.0)

    def test_floor_fill_exceeds_cap_and_is_flagged(self):
        # 100 human labels < min_samples=200 → pseudo may fill to 200 even
        # though the cap alone would only allow ~42.
        samples, labels, prov = _pool(80, 20, 1000)
        out_s, _, out_p, _, comp = apply_composition_policy(
            samples, labels, prov, **_POLICY,
        )
        assert len(out_s) == 200
        assert comp["pseudo_included"] == 100
        assert comp["cap_exceeded_to_fill_floor"] is True
        # fraction recorded honestly (50% pseudo)
        assert comp["fractions"][LLM_PSEUDO] == 0.5

    def test_all_human_path(self):
        samples, labels, prov = _pool(300, 0, 0)
        out_s, _, out_p, weights, comp = apply_composition_policy(
            samples, labels, prov, **_POLICY,
        )
        assert len(out_s) == 300
        assert all(w == 1.0 for w in weights)
        assert comp["counts"][LLM_PSEUDO] == 0
        assert comp["fractions"][HUMAN_DIRECT] == 1.0
        assert comp["bootstrap"] is False

    def test_zero_human_bootstrap_path(self):
        # No human labels: pseudo fills only up to min_samples, and the
        # composition record marks the model as bootstrap.
        samples, labels, prov = _pool(0, 0, 800)
        out_s, _, out_p, weights, comp = apply_composition_policy(
            samples, labels, prov, **_POLICY,
        )
        assert len(out_s) == 200
        assert all(p == LLM_PSEUDO for p in out_p)
        assert all(w == 0.3 for w in weights)
        assert comp["bootstrap"] is True
        assert comp["cap_exceeded_to_fill_floor"] is True
        assert comp["human_label_count"] == 0

    def test_below_floor_flagged_bootstrap_even_with_some_humans(self):
        samples, labels, prov = _pool(30, 10, 400)
        *_, comp = apply_composition_policy(samples, labels, prov, **_POLICY)
        assert comp["human_label_count"] == 40
        assert comp["bootstrap"] is True  # 40 < floor of 50

    def test_deterministic_pseudo_selection(self):
        samples, labels, prov = _pool(100, 0, 500)
        first = apply_composition_policy(samples, labels, prov, **_POLICY)
        second = apply_composition_policy(samples, labels, prov, **_POLICY)
        assert first[0] == second[0] and first[3] == second[3]

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            apply_composition_policy([{}], ["A", "B"], [HUMAN_DIRECT], **_POLICY)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Model maturity derivation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestModelMaturity:
    def test_no_metadata_is_not_trained(self):
        assert model_maturity_from_metadata(None, 50) == MATURITY_NOT_TRAINED
        assert model_maturity_from_metadata({"status": "not_trained"}, 50) == MATURITY_NOT_TRAINED

    def test_legacy_model_without_composition_is_bootstrap(self):
        """Pre-AI-F1 models were trained pseudo-heavy — never promote them."""
        meta = {"status": "trained", "accuracy": 0.9}
        assert model_maturity_from_metadata(meta, 50) == MATURITY_BOOTSTRAP

    def test_below_floor_is_bootstrap(self):
        meta = {"status": "trained", "label_composition": {"human_label_count": 49}}
        assert model_maturity_from_metadata(meta, 50) == MATURITY_BOOTSTRAP

    def test_at_floor_is_human_calibrated(self):
        meta = {"status": "trained", "label_composition": {"human_label_count": 50}}
        assert model_maturity_from_metadata(meta, 50) == MATURITY_HUMAN_CALIBRATED


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Trainer persists the composition into the deployed model metadata
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _separable_pool(n_human: int, n_pseudo: int):
    """Cleanly separable two-class pool so trained accuracy is ~1.0."""
    base = {name: 0.0 for name in FEATURE_NAMES}
    samples, labels, provenances = [], [], []
    for i in range(n_human + n_pseudo):
        s = dict(base)
        if i % 2 == 0:
            s["has_timeout_keyword"] = 1.0
            label = "INFRASTRUCTURE"
        else:
            s["has_assertion_keyword"] = 1.0
            label = "PRODUCT_BUG"
        s["error_msg_length"] = float(i % 7)  # mild variation
        samples.append(s)
        labels.append(label)
        provenances.append(HUMAN_DIRECT if i < n_human else LLM_PSEUDO)
    return samples, labels, provenances


class TestTrainerRecordsComposition:
    @pytest.mark.asyncio
    async def test_metadata_records_label_composition(self, monkeypatch, tmp_path):
        pytest.importorskip("sklearn")
        pytest.importorskip("numpy")
        pytest.importorskip("joblib")
        from app.core.config import settings
        from app.services.ml import trainer

        samples, labels, provenances = _separable_pool(n_human=200, n_pseudo=100)

        async def _fake_gather():
            return samples, labels, provenances

        monkeypatch.setattr(trainer, "_gather_training_data", _fake_gather)
        monkeypatch.setattr(settings, "ML_MODEL_DIR", str(tmp_path))

        result = await trainer.train_classifier()
        assert result["status"] == "trained", result
        comp = result["label_composition"]
        assert comp["human_label_count"] == 200
        # cap: pseudo <= 0.3/0.7 * 200 = 85
        assert comp["pseudo_included"] == 85
        assert comp["counts"][HUMAN_DIRECT] == 200
        assert comp["counts"][LLM_PSEUDO] == 85
        assert comp["bootstrap"] is False

        meta = json.loads((tmp_path / "training_metadata.json").read_text())
        assert meta["label_composition"] == comp
        assert meta["label_composition"]["pseudo_cap"] == settings.ML_PSEUDO_LABEL_CAP
        assert meta["label_composition"]["pseudo_weight"] == settings.ML_PSEUDO_LABEL_WEIGHT

    @pytest.mark.asyncio
    async def test_insufficient_data_still_reports_composition(self, monkeypatch):
        pytest.importorskip("sklearn")
        pytest.importorskip("numpy")
        from app.services.ml import trainer

        samples, labels, provenances = _separable_pool(n_human=20, n_pseudo=30)

        async def _fake_gather():
            return samples, labels, provenances

        monkeypatch.setattr(trainer, "_gather_training_data", _fake_gather)
        result = await trainer.train_classifier()
        assert result["status"] == "insufficient_data"
        assert result["label_composition"]["human_label_count"] == 20
        assert result["label_composition"]["bootstrap"] is True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Routing decision record carries the honesty tag
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRoutingRecordMaturity:
    @pytest.fixture
    def _ml_classifies(self, monkeypatch):
        from app.services.ml.classifier import MLClassifier

        monkeypatch.setattr(
            MLClassifier, "classify",
            staticmethod(lambda features: {
                "failure_category": "INFRASTRUCTURE",
                "confidence_score": 90,
                "classified_by": "ml_classifier",
            }),
        )
        return MLClassifier

    @pytest.mark.asyncio
    async def test_bootstrap_caveat_below_floor(self, monkeypatch, _ml_classifies):
        from app.services.analysis_router import classify_test

        monkeypatch.setattr(
            _ml_classifies, "get_model_info",
            staticmethod(lambda: {
                "status": "trained",
                "label_composition": {"human_label_count": 3},
            }),
        )
        result = await classify_test(
            test_case={"error_message": "Connection refused", "test_name": "t"},
            mode="ml",
        )
        assert result["_routing"]["mode_used"] == "ml"
        assert result["_routing"]["ml_maturity"] == MATURITY_BOOTSTRAP

    @pytest.mark.asyncio
    async def test_human_calibrated_above_floor(self, monkeypatch, _ml_classifies):
        from app.services.analysis_router import classify_test

        monkeypatch.setattr(
            _ml_classifies, "get_model_info",
            staticmethod(lambda: {
                "status": "trained",
                "label_composition": {"human_label_count": 500},
            }),
        )
        result = await classify_test(
            test_case={"error_message": "Connection refused", "test_name": "t"},
            mode="ml",
        )
        assert result["_routing"]["ml_maturity"] == MATURITY_HUMAN_CALIBRATED


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Label-health API surface
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeDB:
    """Stub AsyncSession: first execute → grouped feedback counts, second →
    pseudo-candidate count."""

    def __init__(self, grouped_rows, pseudo_count):
        self._results = [
            _FakeResult(rows=grouped_rows),
            _FakeResult(scalar=pseudo_count),
        ]

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0)


class TestGetLabelHealth:
    @pytest.mark.asyncio
    async def test_counts_bucketed_by_provenance(self, monkeypatch):
        from app.services import ai_eval_service
        from app.services.ml.classifier import MLClassifier

        monkeypatch.setattr(
            MLClassifier, "get_model_info",
            staticmethod(lambda: {
                "status": "trained",
                "label_composition": {"human_label_count": 60, "total": 100},
            }),
        )
        db = _FakeDB(
            grouped_rows=[
                ("manual", 30),
                ("category_correction", 12),
                ("jira_resolved", 8),
                ("jira_invalid", 2),
            ],
            pseudo_count=400,
        )
        health = await ai_eval_service.get_label_health(db)
        assert health["human_direct"] == 42
        assert health["human_indirect"] == 10
        assert health["human_label_total"] == 52
        assert health["llm_pseudo_candidates"] == 400
        assert health["meets_human_label_floor"] is True  # floor default 50
        assert health["ml_maturity"] == MATURITY_HUMAN_CALIBRATED
        assert health["last_trained_composition"]["human_label_count"] == 60
        assert health["human_share_of_pool"] == round(52 / 452, 4)

    @pytest.mark.asyncio
    async def test_bootstrap_reported_below_floor(self, monkeypatch):
        from app.services import ai_eval_service
        from app.services.ml.classifier import MLClassifier

        monkeypatch.setattr(
            MLClassifier, "get_model_info",
            staticmethod(lambda: {
                "status": "trained",
                "label_composition": {"human_label_count": 5},
            }),
        )
        db = _FakeDB(grouped_rows=[("manual", 5)], pseudo_count=300)
        health = await ai_eval_service.get_label_health(db)
        assert health["human_label_total"] == 5
        assert health["meets_human_label_floor"] is False
        assert health["ml_maturity"] == MATURITY_BOOTSTRAP


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Regression: human-weighted beats pseudo-heavy on a human-labeled holdout
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _biased_fixture():
    """Synthetic pool where LLM pseudo-labels are systematically wrong.

    Ground truth: timeout errors are INFRASTRUCTURE, assertion errors are
    PRODUCT_BUG. Human labels follow the truth; the LLM mislabels timeout
    failures as PRODUCT_BUG (a realistic bias: assertion-shaped reasoning).
    """
    base = {name: 0.0 for name in FEATURE_NAMES}
    samples, labels, provenances = [], [], []

    def add(n, *, timeout: bool, label: str, provenance: str):
        for i in range(n):
            s = dict(base)
            if timeout:
                s["has_timeout_keyword"] = 1.0
            else:
                s["has_assertion_keyword"] = 1.0
            # Quantized variation shared across human and pseudo groups, so
            # the tree cannot carve pseudo-only subregions along this axis.
            s["error_msg_length"] = float(40 + (i % 5) * 5)
            samples.append(s)
            labels.append(label)
            provenances.append(provenance)

    # Human ground truth (correct)
    add(40, timeout=True, label="INFRASTRUCTURE", provenance=HUMAN_DIRECT)
    add(40, timeout=False, label="PRODUCT_BUG", provenance=HUMAN_DIRECT)
    # LLM pseudo-labels: timeouts systematically mislabeled
    add(150, timeout=True, label="PRODUCT_BUG", provenance=LLM_PSEUDO)
    add(50, timeout=False, label="PRODUCT_BUG", provenance=LLM_PSEUDO)
    return samples, labels, provenances


@pytest.mark.regression
class TestLabelStrategyComparison:
    """AI-F1 eval evidence: on the same pool, capping + down-weighting
    pseudo-labels yields a model closer to human ground truth than the old
    uniform pseudo-heavy training regime."""

    def test_human_weighted_beats_pseudo_heavy_on_biased_pool(self):
        pytest.importorskip("sklearn")
        pytest.importorskip("numpy")
        from app.services.ml.label_eval import compare_label_strategies

        samples, labels, provenances = _biased_fixture()
        result = compare_label_strategies(
            samples, labels, provenances,
            pseudo_cap=0.30, pseudo_weight=0.3, min_samples=0,
            holdout_fraction=0.3, random_state=42,
        )

        assert result["holdout_provenance"] == "human_only"
        assert result["holdout_size"] >= 20
        # The pseudo-heavy model learned the LLM's bias (timeout → PRODUCT_BUG);
        # the human-weighted model recovers the human ground truth.
        assert result["delta"]["accuracy"] > 0
        infra_delta = result["delta"]["per_class"]["INFRASTRUCTURE"]
        assert infra_delta["recall"] > 0
        assert (
            result["human_weighted"]["per_class"]["INFRASTRUCTURE"]["recall"]
            >= 0.9
        )
        # Composition of the human-weighted strategy is reported for audit
        comp = result["human_weighted_composition"]
        assert comp["pseudo_included"] <= comp["pseudo_available"]
        assert comp["pseudo_weight"] == 0.3

    def test_comparison_requires_enough_human_labels(self):
        pytest.importorskip("sklearn")
        pytest.importorskip("numpy")
        from app.services.ml.label_eval import compare_label_strategies

        base = {name: 0.0 for name in FEATURE_NAMES}
        samples = [dict(base) for _ in range(20)]
        labels = ["PRODUCT_BUG"] * 20
        provenances = [LLM_PSEUDO] * 20
        with pytest.raises(ValueError):
            compare_label_strategies(samples, labels, provenances)
