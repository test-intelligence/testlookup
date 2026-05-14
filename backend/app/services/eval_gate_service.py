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
import hashlib
import json
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


DEFAULT_AGENT_STACK_GATES: tuple[dict[str, str], ...] = (
    {"task_type": "classification", "agent_name": "AnalysisAgent"},
    {"task_type": "root_cause", "agent_name": "AnalysisAgent"},
    {"task_type": "duplicate_detection", "agent_name": "DefectPromotionAgent"},
    {"task_type": "release_decision", "agent_name": "ReleaseRiskAgent"},
)
_BLOCKING_GATE_STATUSES = {GateStatus.FAIL, GateStatus.NO_BASELINE}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _manifest_checksum(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()


def build_agent_stack_gate_manifest(
    *,
    change_id: str,
    prompt_versions: Optional[dict[str, str]] = None,
    model_versions: Optional[dict[str, str]] = None,
    routing_versions: Optional[dict[str, str]] = None,
    required_gates: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Build a deterministic manifest for prompt/model/routing changes."""
    gates = required_gates or [dict(gate) for gate in DEFAULT_AGENT_STACK_GATES]
    gates = sorted(
        [
            {
                "task_type": str(gate["task_type"]),
                "agent_name": str(gate["agent_name"]),
                **({"dataset_id": str(gate["dataset_id"])} if gate.get("dataset_id") else {}),
            }
            for gate in gates
        ],
        key=lambda gate: (gate["task_type"], gate["agent_name"], gate.get("dataset_id", "")),
    )
    manifest = {
        "schema_version": 1,
        "change_id": change_id,
        "prompt_versions": dict(sorted((prompt_versions or {}).items())),
        "model_versions": dict(sorted((model_versions or {}).items())),
        "routing_versions": dict(sorted((routing_versions or {}).items())),
        "required_gates": gates,
    }
    manifest["manifest_checksum_sha256"] = _manifest_checksum(manifest)
    return manifest


def _version_change_summary(
    manifest: dict[str, Any],
    gate_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compare requested prompt/model/routing versions to active baselines."""
    prompt_versions = manifest.get("prompt_versions") or {}
    model_versions = manifest.get("model_versions") or {}
    routing_versions = manifest.get("routing_versions") or {}
    changes: list[dict[str, Any]] = []

    for result in sorted(
        gate_results,
        key=lambda item: (str(item.get("task_type")), str(item.get("agent_name"))),
    ):
        agent_name = result.get("agent_name")
        baseline = result.get("baseline_metrics") or {}
        changes.append({
            "task_type": result.get("task_type"),
            "agent_name": agent_name,
            "prompt_version": {
                "candidate": prompt_versions.get(agent_name),
                "baseline": baseline.get("prompt_version"),
                "changed": prompt_versions.get(agent_name) != baseline.get("prompt_version"),
            },
            "model_name": {
                "candidate": model_versions.get(agent_name),
                "baseline": baseline.get("model_name"),
                "changed": model_versions.get(agent_name) != baseline.get("model_name"),
            },
            "routing_version": {
                "candidate": routing_versions.get(agent_name),
                "baseline": None,
                "changed": bool(routing_versions.get(agent_name)),
            },
        })
    return changes


def _overall_manifest_status(gate_results: list[dict[str, Any]]) -> str:
    statuses = [result.get("status") for result in gate_results]
    if any(status in _BLOCKING_GATE_STATUSES for status in statuses):
        return GateStatus.FAIL
    if any(status == GateStatus.WARN for status in statuses):
        return GateStatus.WARN
    return GateStatus.PASS


async def evaluate_agent_stack_release_gate(
    db: AsyncSession,
    *,
    change_id: str,
    prompt_versions: Optional[dict[str, str]] = None,
    model_versions: Optional[dict[str, str]] = None,
    routing_versions: Optional[dict[str, str]] = None,
    required_gates: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Evaluate all required agent-stack gates before shipping a change."""
    manifest = build_agent_stack_gate_manifest(
        change_id=change_id,
        prompt_versions=prompt_versions,
        model_versions=model_versions,
        routing_versions=routing_versions,
        required_gates=required_gates,
    )

    gate_results: list[dict[str, Any]] = []
    for gate in manifest["required_gates"]:
        gate_results.append(
            await evaluate_pre_release_gate(
                db,
                task_type=gate["task_type"],
                agent_name=gate["agent_name"],
                dataset_id=gate.get("dataset_id"),
            )
        )

    blocking_gates = [
        {
            "task_type": result.get("task_type"),
            "agent_name": result.get("agent_name"),
            "status": result.get("status"),
        }
        for result in gate_results
        if result.get("status") in _BLOCKING_GATE_STATUSES
    ]
    return {
        "status": _overall_manifest_status(gate_results),
        "manifest": manifest,
        "gate_results": gate_results,
        "blocking_gates": blocking_gates,
        "version_changes": _version_change_summary(manifest, gate_results),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


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
