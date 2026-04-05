"""
TrainingDataExporter — collects verified examples from the application's own data
and exports them as JSONL files to MinIO for each training track.

Data sources:
  Track 1 (classifier):  ai_analysis JOIN defects WHERE resolution_status=RESOLVED
                         + ai_feedback WHERE rating=correct
                         + ai_feedback WHERE rating=incorrect + corrected_category
  Track 2 (reasoning):   MongoDB AI_ANALYSIS_PAYLOADS (full ReAct traces) for
                         verified high-confidence analyses
  Track 3 (embedding):   Contrastive pairs derived from test_case_history grouped
                         by failure_category + test_fingerprint patterns

Output format (per track):
  training-data/classifier/YYYY-MM-DD.jsonl
  training-data/reasoning/YYYY-MM-DD.jsonl
  training-data/embedding/YYYY-MM-DD.jsonl
  training-data/classifier/holdout.jsonl  (10%, never trained on)
"""
import json
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import select, and_, update as sa_update

from app.core.config import settings
from app.db.mongo import Collections, get_mongo_db
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import AIAnalysis, AIFeedback, Defect, FeedbackRating, TestCase

logger = logging.getLogger("training.exporter")


class TrainingDataExporter:
    """
    Exports verified training examples to MinIO as JSONL files.
    Called weekly by the Celery beat schedule.
    """

    async def run(self) -> dict[str, int]:
        """Export all three tracks. Returns {track: example_count}."""
        counts: dict[str, int] = {}
        counts["classifier"] = await self._export_classifier()
        counts["reasoning"] = await self._export_reasoning()
        counts["embedding"] = await self._export_embedding()
        logger.info("Training export complete: %s", counts)
        return counts

    # ── Track 1: Classifier ───────────────────────────────────────────────────

    async def _export_classifier(self) -> int:
        """
        Collect (error_message, stack_trace) → failure_category examples.
        Sources:
          1. Resolved defects with high-confidence AI analyses (ground truth = Jira)
          2. Explicit 'correct' feedback from engineers
          3. Category corrections (rating=incorrect + corrected_category set)
        """
        examples: list[dict] = []

        async with AsyncSessionLocal() as db:
            # Source 1: resolved defects → positive examples
            rows = await db.execute(
                select(
                    TestCase.test_name,
                    TestCase.error_message,
                    AIAnalysis.failure_category,
                    AIAnalysis.root_cause_summary,
                    AIAnalysis.confidence_score,
                )
                .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .join(Defect, Defect.test_case_id == TestCase.id)
                .where(
                    and_(
                        Defect.resolution_status == "RESOLVED",
                        AIAnalysis.confidence_score >= 70,
                        AIAnalysis.failure_category.isnot(None),
                        AIAnalysis.failure_category != "UNKNOWN",
                        TestCase.error_message.isnot(None),
                    )
                )
            )
            for row in rows.all():
                examples.append(self._make_classifier_example(
                    test_name=row.test_name,
                    error_message=row.error_message or "",
                    category=row.failure_category,
                    root_cause=row.root_cause_summary or "",
                    confidence=row.confidence_score or 0,
                    source="jira_resolved",
                ))

            # Source 2: explicit 'correct' ratings
            correct_rows = await db.execute(
                select(
                    TestCase.test_name,
                    TestCase.error_message,
                    AIAnalysis.failure_category,
                    AIAnalysis.root_cause_summary,
                    AIAnalysis.confidence_score,
                )
                .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .join(AIFeedback, AIFeedback.analysis_id == AIAnalysis.id)
                .where(
                    and_(
                        AIFeedback.rating == FeedbackRating.CORRECT,
                        AIAnalysis.failure_category.isnot(None),
                        TestCase.error_message.isnot(None),
                    )
                )
            )
            for row in correct_rows.all():
                examples.append(self._make_classifier_example(
                    test_name=row.test_name,
                    error_message=row.error_message or "",
                    category=row.failure_category,
                    root_cause=row.root_cause_summary or "",
                    confidence=row.confidence_score or 0,
                    source="manual_correct",
                ))

            # Source 3: corrections → use corrected values as gold label
            correction_rows = await db.execute(
                select(
                    TestCase.test_name,
                    TestCase.error_message,
                    AIFeedback.corrected_category,
                    AIFeedback.corrected_root_cause,
                )
                .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .join(AIFeedback, AIFeedback.analysis_id == AIAnalysis.id)
                .where(
                    and_(
                        AIFeedback.rating == FeedbackRating.INCORRECT,
                        AIFeedback.corrected_category.isnot(None),
                        TestCase.error_message.isnot(None),
                    )
                )
            )
            for corr_row in correction_rows.all():
                examples.append(self._make_classifier_example(
                    test_name=corr_row.test_name,
                    error_message=corr_row.error_message or "",
                    category=str(corr_row.corrected_category),
                    root_cause=corr_row.corrected_root_cause or "",
                    confidence=100,  # human correction = perfect label
                    source="category_correction",
                ))

            # Mark as exported
            feedback_ids = await db.execute(
                select(AIFeedback.id).where(AIFeedback.exported.is_(False))
            )
            ids = [r[0] for r in feedback_ids.all()]
            if ids:
                await db.execute(
                    sa_update(AIFeedback)
                    .where(AIFeedback.id.in_(ids))
                    .values(exported=True)
                )
                await db.commit()

        if not examples:
            logger.info("Classifier export: no new examples")
            return 0

        return await self._write_jsonl("classifier", examples)

    # ── Track 2: Reasoning ────────────────────────────────────────────────────

    async def _export_reasoning(self) -> int:
        """
        Export verified ReAct traces from MongoDB.
        Only exports traces where the corresponding Defect was resolved.
        """
        db = get_mongo_db()
        examples: list[dict] = []

        # Fetch all high-confidence traces
        cursor = db[Collections.AI_ANALYSIS_PAYLOADS].find(
            {"analysis.confidence_score": {"$gte": 85}},
            {"test_case_id": 1, "prompt": 1, "intermediate_steps": 1, "analysis": 1, "llm_model": 1},
        ).limit(5000)

        # Cross-reference with resolved defects in PostgreSQL
        async with AsyncSessionLocal() as pg_db:
            resolved_ids_result = await pg_db.execute(
                select(Defect.test_case_id.cast(str))
                .where(Defect.resolution_status == "RESOLVED")
            )
            resolved_ids = {str(r[0]) for r in resolved_ids_result.all()}

        async for doc in cursor:
            test_case_id = str(doc.get("test_case_id", ""))
            if test_case_id not in resolved_ids:
                continue

            analysis = doc.get("analysis", {})
            if not analysis.get("failure_category") or analysis.get("failure_category") == "UNKNOWN":
                continue

            examples.append({
                "messages": [
                    {"role": "system", "content": _REASONING_SYSTEM_PROMPT},
                    {"role": "user", "content": doc.get("prompt", "")},
                    # Reconstruct the tool-calling chain as assistant turns
                    *self._format_reasoning_chain(doc.get("intermediate_steps", [])),
                    {"role": "assistant", "content": json.dumps(analysis, indent=2)},
                ]
            })

        if not examples:
            logger.info("Reasoning export: no new verified traces")
            return 0

        return await self._write_jsonl("reasoning", examples)

    # ── Track 3: Embedding ────────────────────────────────────────────────────

    async def _export_embedding(self) -> int:
        """
        Build contrastive pairs for embedding fine-tuning.
        Positive pairs: same failure_category (different tests, similar root cause)
        Negative pairs: different failure_category (or same category but different mechanism)
        """
        examples: list[dict] = []

        async with AsyncSessionLocal() as db:
            rows = await db.execute(
                select(
                    TestCase.test_name,
                    TestCase.error_message,
                    AIAnalysis.failure_category,
                    AIAnalysis.root_cause_summary,
                )
                .join(AIAnalysis, AIAnalysis.test_case_id == TestCase.id)
                .join(Defect, Defect.test_case_id == TestCase.id)
                .where(
                    and_(
                        Defect.resolution_status == "RESOLVED",
                        AIAnalysis.failure_category.isnot(None),
                        AIAnalysis.failure_category != "UNKNOWN",
                        TestCase.error_message.isnot(None),
                    )
                )
                .limit(10000)
            )
            data = rows.all()

        # Group by category
        by_category: dict[str, list] = {}
        for row in data:
            cat = str(row.failure_category)
            by_category.setdefault(cat, []).append({
                "text": f"{row.test_name}: {row.error_message}",
                "summary": row.root_cause_summary or "",
            })

        categories = list(by_category.keys())
        for cat, items in by_category.items():
            if len(items) < 2:
                continue

            # Positive pairs within same category
            random.shuffle(items)
            for i in range(min(len(items) - 1, 100)):
                examples.append({
                    "anchor": items[i]["text"],
                    "positive": items[i + 1]["text"],
                    "negative": None,  # filled below
                    "label": cat,
                })

            # Negative: pick from a different category
            other_cats = [c for c in categories if c != cat and by_category[c]]
            if other_cats:
                neg_cat = random.choice(other_cats)
                neg_pool = by_category[neg_cat]
                for ex in examples[-100:]:
                    ex["negative"] = random.choice(neg_pool)["text"]

        # Prune examples without negatives
        examples = [e for e in examples if e.get("negative")]

        if not examples:
            logger.info("Embedding export: not enough category diversity yet")
            return 0

        return await self._write_jsonl("embedding", examples)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_classifier_example(
        test_name: str,
        error_message: str,
        category: str,
        root_cause: str,
        confidence: int,
        source: str,
    ) -> dict:
        return {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a test failure classifier. Classify the test failure into one of: "
                        "PRODUCT_BUG, INFRASTRUCTURE, TEST_DATA, AUTOMATION_DEFECT, FLAKY, UNKNOWN. "
                        "Return JSON: {\"category\": \"...\", \"confidence\": 0-100, \"reasoning\": \"...\"}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Test: {test_name}\nError: {error_message[:2000]}",
                },
                {
                    "role": "assistant",
                    "content": json.dumps({
                        "category": category,
                        "confidence": confidence,
                        "reasoning": root_cause[:500],
                    }),
                },
            ],
            "_meta": {"source": source},
        }

    @staticmethod
    def _format_reasoning_chain(steps: list) -> list[dict]:
        """Convert stored intermediate_steps strings into assistant message turns."""
        turns = []
        for step in steps:
            step_str = str(step)
            if step_str.strip():
                turns.append({"role": "assistant", "content": step_str})
        return turns

    async def _write_jsonl(self, track: str, examples: list[dict]) -> int:
        """
        Shuffle, split train/holdout, upload to MinIO.
        Returns number of training examples written.
        """
        random.shuffle(examples)
        holdout_n = max(1, int(len(examples) * settings.FINETUNE_EVAL_HOLDOUT))
        holdout = examples[:holdout_n]
        train = examples[holdout_n:]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        train_path = f"{track}/{today}.jsonl"
        holdout_path = f"{track}/holdout-{today}.jsonl"

        await self._upload_jsonl(train_path, train)
        await self._upload_jsonl(holdout_path, holdout)

        logger.info(
            "Exported track=%s train=%d holdout=%d → %s",
            track, len(train), len(holdout), train_path,
        )
        return len(train)

    @staticmethod
    async def _upload_jsonl(path: str, records: list[dict]) -> None:
        """Upload JSONL to MinIO training-data bucket."""
        from app.db.storage import get_storage_provider
        storage = get_storage_provider()
        content = "\n".join(json.dumps(r) for r in records).encode("utf-8")
        await storage.put_object(
            key=path,
            content_type="application/jsonl",
            content=content,
            bucket=settings.FINETUNE_EXPORT_BUCKET,
        )


# System prompt used when formatting reasoning track examples
_REASONING_SYSTEM_PROMPT = (
    "You are an expert QA/SRE analyst. Use your investigation tools to determine "
    "the root cause of the failing test and return a structured JSON analysis."
)
