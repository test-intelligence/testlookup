"""
ML-enhanced summary generator — produces 4-layer structured reports from
ML classification outputs without any LLM calls.

Uses the RulesEngine template system enriched with ML confidence scores
and prediction metadata. The output shape is identical to LLM-generated
summaries for full backward compatibility with the frontend and reports.

Usage:
    from app.services.ml.summary_generator import MLSummaryGenerator
    summary = MLSummaryGenerator.generate(run_data, ml_classifications, anomalies)
"""
from typing import Any

from app.services.rules_engine import RulesEngine


class MLSummaryGenerator:
    """Generate structured summaries from ML classification results.

    Delegates to the RulesEngine template system but enriches the output
    with ML-specific metadata (model version, average confidence, etc.).
    """

    @staticmethod
    def generate(
        run_data: dict[str, Any],
        classifications: dict[str, dict[str, Any]],
        anomalies: list[dict] | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any]:
        """Generate a 4-layer structured summary from ML classifications.

        Same output shape as SummaryAgent._build_fallback_structured_report().
        """
        # Use the rules engine template system for summary generation
        summary = RulesEngine.generate_summary(run_data, classifications, anomalies)

        # Enrich with ML metadata
        avg_confidence = 0.0
        ml_count = 0
        for cls in classifications.values():
            if cls.get("classified_by") in ("ml_classifier", "rules_engine"):
                avg_confidence += cls.get("confidence_score", 0)
                ml_count += 1
        if ml_count > 0:
            avg_confidence /= ml_count

        # Append ML confidence note to executive summary
        layer1 = summary["layer1_executive_summary"]
        mode_label = "ML classifier" if any(
            c.get("classified_by") == "ml_classifier" for c in classifications.values()
        ) else "rules engine"

        summary["layer1_executive_summary"] = (
            f"{layer1} "
            f"(Analysis by {mode_label}"
            f"{f' v{model_version}' if model_version else ''}"
            f", avg confidence: {avg_confidence:.0f}%)"
        )

        return summary
