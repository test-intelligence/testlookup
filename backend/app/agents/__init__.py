"""Multi-agent system for test report processing and analysis.

Keep package imports lazy to avoid importing optional dependencies (e.g. motor)
during unrelated unit tests.
"""
import importlib

__all__ = ["QueryIntent", "classify_intent", "run_offline_pipeline", "run_deep_pipeline"]

# Workflow-level node function names that tests may import
_WORKFLOW_EXPORTS = {
    "run_offline_pipeline", "run_deep_pipeline",
    "analysis_node", "cluster_node", "summary_node",
    "triage_node", "anomaly_node", "ingestion_node",
}


def __getattr__(name: str):
    if name in {"QueryIntent", "classify_intent"}:
        from app.agents.conversation import QueryIntent, classify_intent

        exports = {
            "QueryIntent": QueryIntent,
            "classify_intent": classify_intent,
        }
        return exports[name]
    if name == "workflow":
        return importlib.import_module("app.agents.workflow")
    if name in _WORKFLOW_EXPORTS:
        _wf = importlib.import_module("app.agents.workflow")

        return getattr(_wf, name)
    raise AttributeError(f"module 'app.agents' has no attribute {name}")
