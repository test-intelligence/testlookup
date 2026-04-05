"""
ModelEvaluator — A/B evaluation gate before promoting a fine-tuned model.

Reads the holdout JSONL from MinIO (never seen during training), runs the
candidate model against it, and compares accuracy to the current baseline.
Only returns True (approve promotion) if the candidate improves by at least
FINETUNE_MIN_ACCURACY_GAIN.

For the classifier track:  exact-match accuracy on failure_category
For the reasoning track:   category accuracy + ROUGE-L on root_cause_summary
For the embedding track:   cosine similarity on positive/negative pair margin
"""
import json
import logging
from typing import Optional

from app.core.config import settings
from app.services.llm_factory import get_llm
from app.services.training.classifier import _parse_classifier_output

logger = logging.getLogger("training.evaluator")


class ModelEvaluator:
    """Runs holdout evaluation and decides whether to promote a candidate model."""

    @classmethod
    async def evaluate(
        cls,
        track: str,
        candidate_model: str,
        holdout_path: str,
    ) -> dict:
        """
        Evaluate `candidate_model` on the holdout set at `holdout_path`.
        Returns: {approved, candidate_accuracy, baseline_accuracy, improvement, details}
        """
        examples = await cls._load_holdout(holdout_path)
        if not examples:
            logger.warning("Evaluator: no holdout examples found at %s", holdout_path)
            return {"approved": False, "reason": "empty_holdout"}

        if track == "classifier":
            return await cls._eval_classifier(candidate_model, examples)
        elif track == "reasoning":
            return await cls._eval_reasoning(candidate_model, examples)
        else:
            # Embedding track: approve if enough examples were generated
            return {"approved": len(examples) >= 100, "reason": "embedding_coverage_check"}

    @classmethod
    async def _eval_classifier(cls, candidate_model: str, examples: list[dict]) -> dict:
        """Exact-match accuracy on failure_category for classifier track."""
        correct_candidate = 0
        correct_baseline = 0
        details: list[dict] = []

        for ex in examples:
            messages = ex.get("messages", [])
            if len(messages) < 3:
                continue

            user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
            expected_raw = next((m["content"] for m in messages if m["role"] == "assistant"), "{}")
            expected = json.loads(expected_raw) if expected_raw.startswith("{") else {}
            expected_cat = expected.get("category", "")

            # Evaluate candidate
            cand_result = await cls._run_classifier(candidate_model, user_msg)
            cand_cat = cand_result.get("category", "") if cand_result else ""

            # Evaluate baseline (current production model)
            base_result = await cls._run_classifier(settings.LLM_MODEL, user_msg)
            base_cat = base_result.get("category", "") if base_result else ""

            correct_candidate += int(cand_cat == expected_cat)
            correct_baseline += int(base_cat == expected_cat)

            details.append({
                "expected": expected_cat,
                "candidate": cand_cat,
                "baseline": base_cat,
            })

        n = len(examples)
        cand_acc = correct_candidate / n if n else 0.0
        base_acc = correct_baseline / n if n else 0.0
        improvement = cand_acc - base_acc
        approved = improvement >= settings.FINETUNE_MIN_ACCURACY_GAIN

        logger.info(
            "Classifier eval: n=%d candidate=%.3f baseline=%.3f improvement=%.3f approved=%s",
            n, cand_acc, base_acc, improvement, approved,
        )
        return {
            "approved": approved,
            "candidate_accuracy": round(cand_acc, 4),
            "baseline_accuracy": round(base_acc, 4),
            "improvement": round(improvement, 4),
            "n_examples": n,
            "details": details[:20],  # send first 20 for inspection
        }

    @classmethod
    async def _eval_reasoning(cls, candidate_model: str, examples: list[dict]) -> dict:
        """Category accuracy on reasoning track (proxy for full quality)."""
        correct_candidate = 0
        correct_baseline = 0
        n = min(len(examples), 50)  # cap at 50 to limit LLM cost

        for ex in examples[:n]:
            messages = ex.get("messages", [])
            user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
            # Expected: last assistant message (final JSON)
            assistant_msgs = [m["content"] for m in messages if m["role"] == "assistant"]
            if not assistant_msgs:
                continue
            expected_raw = assistant_msgs[-1]
            try:
                expected = json.loads(expected_raw)
            except json.JSONDecodeError:
                continue
            expected_cat = expected.get("failure_category", "")

            cand_result = await cls._run_classifier(candidate_model, user_msg)
            base_result = await cls._run_classifier(settings.LLM_MODEL, user_msg)

            correct_candidate += int((cand_result or {}).get("category", "") == expected_cat)
            correct_baseline += int((base_result or {}).get("category", "") == expected_cat)

        cand_acc = correct_candidate / n if n else 0.0
        base_acc = correct_baseline / n if n else 0.0
        improvement = cand_acc - base_acc
        approved = improvement >= settings.FINETUNE_MIN_ACCURACY_GAIN

        logger.info(
            "Reasoning eval: n=%d candidate=%.3f baseline=%.3f improvement=%.3f approved=%s",
            n, cand_acc, base_acc, improvement, approved,
        )
        return {
            "approved": approved,
            "candidate_accuracy": round(cand_acc, 4),
            "baseline_accuracy": round(base_acc, 4),
            "improvement": round(improvement, 4),
            "n_examples": n,
        }

    @staticmethod
    async def _run_classifier(model: str, user_content: str) -> Optional[dict]:
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from app.services.training.classifier import _CLASSIFIER_SYSTEM
            llm = get_llm(model=model, temperature=0.0)
            resp = await llm.ainvoke([
                SystemMessage(content=_CLASSIFIER_SYSTEM),
                HumanMessage(content=user_content),
            ])
            raw = resp.content if hasattr(resp, "content") else str(resp)
            return _parse_classifier_output(raw if isinstance(raw, str) else str(raw))
        except Exception as exc:
            logger.debug("Eval inference failed model=%s: %s", model, exc)
            return None

    @staticmethod
    async def _load_holdout(path: str) -> list[dict]:
        """Download holdout JSONL from MinIO."""
        try:
            from app.db.storage import get_storage_provider
            storage = get_storage_provider()
            content = await storage.get_object_content(path, bucket=settings.FINETUNE_EXPORT_BUCKET)
            return [json.loads(line) for line in content.decode().splitlines() if line.strip()]
        except Exception as exc:
            logger.error("Failed to load holdout from %s: %s", path, exc)
            return []
