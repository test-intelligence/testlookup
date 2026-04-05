"""
Evaluation Gate Service — Phase 5: Make Evaluation a Release Gate.

Evaluates agent quality against baselines and configurable thresholds.
Returns PASS/FAIL with per-rule results so that prompt, model, or routing
changes cannot ship without passing quality gates.

Gate flow:
  1. Load active baselines for the specified task_type/agent
  2. Run evaluation against the specified dataset
  3. Compare current metrics to baseline
  4. Apply threshold rules (min_accuracy, min_f1, max_regression_pct)
  5. Return structured gate result
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres import AIEvalBaseline, AIEvalDataset
from app.services.ai_eval_service import compute_metrics_for_task_type

logger = logging.getLogger("services.eval_gate")


class GateStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NO_BASELINE = "NO_BASELINE"


async def evaluate_pre_release_gate(
    db: AsyncSession,
    *,
    task_type: str,
    agent_name: str,
    dataset_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run the pre-release evaluation gate for an agent.

    Steps:
      1. Load the active baseline for this task_type + agent_name
      2. Load the dataset (specified or golden)
      3. Compute metrics
      4. Compare against baseline thresholds
      5. Return structured gate result

    Returns:
        {
            "status": "PASS" | "FAIL" | "WARN" | "NO_BASELINE",
            "task_type": str,
            "agent_name": str,
            "current_metrics": {...},
            "baseline_metrics": {...} | None,
            "rule_results": [{"rule": str, "passed": bool, "detail": str}],
            "evaluated_at": str,
        }
    """
    # 1. Load baseline
    baseline = await _load_active_baseline(db, task_type, agent_name)

    # 2. Load dataset
    items = await _load_dataset_items(db, task_type, dataset_id)
    if not items:
        return {
            "status": GateStatus.FAIL,
            "task_type": task_type,
            "agent_name": agent_name,
            "current_metrics": None,
            "baseline_metrics": None,
            "rule_results": [{"rule": "dataset_exists", "passed": False, "detail": "No evaluation dataset found"}],
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

    # 3. Compute current metrics
    current = compute_metrics_for_task_type(task_type, items)

    # 4. Compare against baseline and thresholds
    rule_results = _evaluate_rules(current, baseline)

    # 5. Determine overall status
    failures = [r for r in rule_results if not r["passed"]]
    warnings = [r for r in rule_results if r.get("severity") == "warn"]

    if not baseline:
        status = GateStatus.NO_BASELINE
    elif failures:
        status = GateStatus.FAIL
    elif warnings:
        status = GateStatus.WARN
    else:
        status = GateStatus.PASS

    baseline_metrics = None
    if baseline:
        baseline_metrics = {
            "accuracy": baseline.baseline_accuracy,
            "precision": baseline.baseline_precision,
            "recall": baseline.baseline_recall,
            "f1_score": baseline.baseline_f1,
            "min_accuracy": baseline.min_accuracy,
            "min_f1": baseline.min_f1,
            "max_regression_pct": baseline.max_regression_pct,
            "prompt_version": baseline.prompt_version,
            "model_name": baseline.model_name,
        }

    return {
        "status": status,
        "task_type": task_type,
        "agent_name": agent_name,
        "current_metrics": current,
        "baseline_metrics": baseline_metrics,
        "rule_results": rule_results,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


async def set_baseline_from_eval(
    db: AsyncSession,
    *,
    task_type: str,
    agent_name: str,
    prompt_version: str = "v1",
    model_name: Optional[str] = None,
    dataset_id: Optional[str] = None,
    min_accuracy: float = 0.80,
    min_f1: float = 0.75,
    max_regression_pct: float = 5.0,
    created_by: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Compute metrics from a dataset and set as the active baseline.

    Deactivates any existing baseline for this task_type + agent_name.
    """
    items = await _load_dataset_items(db, task_type, dataset_id)
    if not items:
        raise ValueError(f"No dataset found for task_type={task_type}")

    metrics = compute_metrics_for_task_type(task_type, items)

    # Deactivate existing baselines
    existing = await db.execute(
        select(AIEvalBaseline).where(
            AIEvalBaseline.task_type == task_type,
            AIEvalBaseline.agent_name == agent_name,
            AIEvalBaseline.is_active.is_(True),
        )
    )
    for b in existing.scalars().all():
        b.is_active = False
        db.add(b)

    import uuid as _uuid

    baseline = AIEvalBaseline(
        task_type=task_type,
        agent_name=agent_name,
        prompt_version=prompt_version,
        model_name=model_name,
        baseline_accuracy=metrics.get("accuracy"),
        baseline_precision=metrics.get("precision"),
        baseline_recall=metrics.get("recall"),
        baseline_f1=metrics.get("f1_score"),
        min_accuracy=min_accuracy,
        min_f1=min_f1,
        max_regression_pct=max_regression_pct,
        dataset_id=_uuid.UUID(dataset_id) if dataset_id else None,
        created_by=created_by,
        is_active=True,
    )
    db.add(baseline)
    await db.commit()
    await db.refresh(baseline)

    return {
        "baseline_id": str(baseline.id),
        "task_type": task_type,
        "agent_name": agent_name,
        "metrics": metrics,
        "thresholds": {
            "min_accuracy": min_accuracy,
            "min_f1": min_f1,
            "max_regression_pct": max_regression_pct,
        },
    }


# ── Internal helpers ─────────────────────────────────────────────────────────


async def _load_active_baseline(
    db: AsyncSession,
    task_type: str,
    agent_name: str,
) -> Optional[AIEvalBaseline]:
    """Load the active baseline for a task_type + agent_name."""
    result = await db.execute(
        select(AIEvalBaseline).where(
            AIEvalBaseline.task_type == task_type,
            AIEvalBaseline.agent_name == agent_name,
            AIEvalBaseline.is_active.is_(True),
        ).order_by(AIEvalBaseline.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def _load_dataset_items(
    db: AsyncSession,
    task_type: str,
    dataset_id: Optional[str] = None,
) -> list[dict]:
    """Load dataset items — from specific dataset or golden defaults."""
    if dataset_id:
        import uuid as _uuid
        result = await db.execute(
            select(AIEvalDataset).where(AIEvalDataset.id == _uuid.UUID(dataset_id))
        )
        dataset = result.scalar_one_or_none()
        if dataset:
            return dataset.items or []

    # Fallback to golden dataset
    from app.services.golden_datasets import GOLDEN_DATASETS
    spec = GOLDEN_DATASETS.get(task_type)
    if spec:
        return spec["get_items"]()

    return []


def _evaluate_rules(
    current: dict,
    baseline: Optional[AIEvalBaseline],
) -> list[dict]:
    """Evaluate threshold and regression rules."""
    rules: list[dict] = []

    accuracy = current.get("accuracy")
    f1 = current.get("f1_score")

    if baseline:
        # Rule 1: Minimum accuracy threshold
        min_acc = baseline.min_accuracy
        if accuracy is not None:
            passed = accuracy >= min_acc
            rules.append({
                "rule": "min_accuracy",
                "passed": passed,
                "detail": f"Accuracy {accuracy:.4f} {'>='.rjust(2)} {min_acc:.2f} threshold" if passed
                else f"Accuracy {accuracy:.4f} below {min_acc:.2f} threshold",
            })

        # Rule 2: Minimum F1 threshold
        min_f1 = baseline.min_f1
        if f1 is not None:
            passed = f1 >= min_f1
            rules.append({
                "rule": "min_f1",
                "passed": passed,
                "detail": f"F1 {f1:.4f} {'>='.rjust(2)} {min_f1:.2f} threshold" if passed
                else f"F1 {f1:.4f} below {min_f1:.2f} threshold",
            })

        # Rule 3: No regression from baseline
        if accuracy is not None and baseline.baseline_accuracy is not None:
            drop_pct = (baseline.baseline_accuracy - accuracy) * 100
            max_drop = baseline.max_regression_pct
            passed = drop_pct <= max_drop
            rules.append({
                "rule": "no_regression",
                "passed": passed,
                "detail": f"Accuracy drop {drop_pct:.1f}% within {max_drop:.1f}% tolerance" if passed
                else f"Accuracy dropped {drop_pct:.1f}% (max allowed: {max_drop:.1f}%)",
                "severity": "warn" if 0 < drop_pct <= max_drop else None,
            })
    else:
        # No baseline — apply default thresholds
        if accuracy is not None:
            passed = accuracy >= 0.70
            rules.append({
                "rule": "default_min_accuracy",
                "passed": passed,
                "detail": f"Accuracy {accuracy:.4f} (no baseline — using 0.70 default)",
            })

    # Rule 4: Dataset has enough items
    total = current.get("total", 0)
    rules.append({
        "rule": "sufficient_data",
        "passed": total >= 3,
        "detail": f"Dataset has {total} items" + ("" if total >= 3 else " (minimum 3 required)"),
    })

    return rules
