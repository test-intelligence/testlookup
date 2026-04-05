"""
Continuous fine-tuning pipeline for TestLookup.

Three training tracks:
  classifier  — fast failure-category classifier (no tool calls, ~50ms)
  reasoning   — full ReAct reasoning model (verified tool-call chains)
  embedding   — domain embedding model (contrastive failure pairs)

Entry points:
  exporter.py   → TrainingDataExporter   (weekly Celery beat task)
  classifier.py → FastClassifier         (fast-path in run_triage_agent)
  finetuner.py  → FineTuningPipeline     (provider-specific training jobs)
  evaluator.py  → ModelEvaluator         (A/B gate before promotion)
"""
