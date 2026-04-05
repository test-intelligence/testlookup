from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

def test_classifier_parsing_and_default_actions():
    from app.services.training.classifier import _default_actions, _parse_classifier_output
    parsed = _parse_classifier_output('prefix {"category":"INFRASTRUCTURE","confidence":91} suffix')
    assert parsed["category"] == "INFRASTRUCTURE"
    assert len(_default_actions("FLAKY")) >= 2


@pytest.mark.asyncio
async def test_fast_classifier_returns_none_below_threshold():
    from app.services.training.classifier import FastClassifier
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"category":"FLAKY","confidence":40,"reasoning":"x"}')))
    with (
        patch("app.services.training.classifier.ModelRegistry.get_active_model", new=AsyncMock(return_value=None)),
        patch("app.services.training.classifier.get_llm", return_value=llm),
        patch("app.services.training.classifier.settings") as s,
    ):
        s.CLASSIFIER_MODEL = None
        s.LLM_MODEL = "base"
        s.CLASSIFIER_CONFIDENCE_THRESHOLD = 80
        s.LLM_PROVIDER = "ollama"
        out = await FastClassifier.classify("t", "e")
    assert out is None


@pytest.mark.asyncio
async def test_test_case_ai_wrapper_parse_failure_fallback():
    from app.services import test_case_ai_agent
    fake_loop = SimpleNamespace(run_in_executor=AsyncMock(return_value="not-json"))
    with patch("asyncio.get_event_loop", return_value=fake_loop):
        out = await test_case_ai_agent.ai_generate_test_cases("req")
    assert out["test_cases"] == []
    assert "error" in out


@pytest.mark.asyncio
async def test_model_evaluator_approves_embedding_by_count():
    from app.services.training.evaluator import ModelEvaluator
    with patch("app.services.training.evaluator.ModelEvaluator._load_holdout", new=AsyncMock(return_value=[{}] * 101)):
        out = await ModelEvaluator.evaluate("embedding", "m1", "x")
    assert out["approved"] is True


@pytest.mark.asyncio
async def test_model_evaluator_classifier_comparison():
    from app.services.training.evaluator import ModelEvaluator
    examples = [
        {"messages": [{"role": "user", "content": "u"}, {"role": "assistant", "content": '{"category":"PRODUCT_BUG"}'}, {"role": "assistant", "content": "{}"}]}
    ]
    with (
        patch("app.services.training.evaluator.ModelEvaluator._run_classifier", new=AsyncMock(side_effect=[{"category": "PRODUCT_BUG"}, {"category": "PRODUCT_BUG"}])),
        patch("app.services.training.evaluator.settings") as s,
    ):
        s.LLM_MODEL = "base"
        s.FINETUNE_MIN_ACCURACY_GAIN = 0.0
        out = await ModelEvaluator._eval_classifier("cand", examples)
    assert out["approved"] is True
    assert out["candidate_accuracy"] == 1.0


@pytest.mark.asyncio
async def test_exporter_write_jsonl_splits_holdout():
    with patch.dict(
        "sys.modules",
        {
            "app.db.mongo": SimpleNamespace(Collections=SimpleNamespace(AI_ANALYSIS_PAYLOADS="ai"), get_mongo_db=lambda: {}),
            "app.db.postgres": SimpleNamespace(AsyncSessionLocal=None),
            "app.models.postgres": SimpleNamespace(
                AIAnalysis=object,
                AIFeedback=object,
                Defect=object,
                FeedbackRating=SimpleNamespace(CORRECT="correct", INCORRECT="incorrect"),
                TestCase=object,
            ),
        },
        clear=False,
    ):
        from app.services.training.exporter import TrainingDataExporter
        exp = TrainingDataExporter()
        uploaded = []

        async def fake_upload(path, records):
            uploaded.append((path, len(records)))

        with patch("app.services.training.exporter.settings") as s:
            s.FINETUNE_EVAL_HOLDOUT = 0.2
            s.FINETUNE_EXPORT_BUCKET = "b"
            with patch.object(exp, "_upload_jsonl", side_effect=fake_upload):
                count = await exp._write_jsonl("classifier", [{"x": i} for i in range(10)])
    assert count == 8
    assert len(uploaded) == 2


@pytest.mark.asyncio
async def test_finetuner_manual_provider():
    from app.services.training.finetuner import FineTuningPipeline
    with patch("app.services.training.finetuner.settings") as s:
        s.LLM_MODEL = "base"
        s.LLM_PROVIDER = "custom"
        s.FINETUNE_EXPORT_BUCKET = "bucket"
        out = await FineTuningPipeline.submit("classifier", "classifier/today.jsonl")
    assert out["status"] == "manual_required"


@pytest.mark.asyncio
async def test_finetuner_poll_non_openai_is_succeeded():
    from app.services.training.finetuner import FineTuningPipeline
    with patch("app.services.training.finetuner.settings") as s:
        s.LLM_PROVIDER = "ollama"
        out = await FineTuningPipeline.poll_status("my-model")
    assert out["status"] == "succeeded"
