"""
Classification benchmark evaluator.

Loads the labelled dataset, runs each case through the rules engine (the
only engine that doesn't require a trained model or a live LLM), and
computes precision/recall/F1 per category plus macro averages.

Usage:
    python benchmarks/classification/evaluate.py [--mode rules|ml|llm|auto] [--output benchmarks/results/classification.json]

The ``--mode ml`` and ``--mode llm`` paths require a running backend
with a trained model / Ollama respectively. ``--mode rules`` (the
default) runs in-process with zero dependencies beyond the backend
Python environment.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


DATASET_PATH = Path(__file__).parent / "dataset.json"
CATEGORIES = ["PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY", "UNKNOWN"]


def load_dataset() -> list[dict[str, Any]]:
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


def classify_via_rules(case: dict[str, Any]) -> dict[str, Any]:
    from app.services.rules_engine import RulesEngine
    return RulesEngine.classify_test(
        error_message=case.get("error_message"),
        test_name=case.get("test_name"),
        duration_ms=case.get("duration_ms"),
        severity=case.get("severity"),
        history=case.get("history"),
        run_context=case.get("run_context"),
    )


def evaluate(cases: list[dict[str, Any]], classify_fn) -> dict[str, Any]:
    """Run the classifier on every case and compute metrics."""
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    correct = 0
    total = len(cases)
    mismatches: list[dict[str, str]] = []
    latencies: list[float] = []

    for case in cases:
        expected = case["label"]
        t0 = time.perf_counter()
        result = classify_fn(case)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

        predicted = result.get("failure_category", "UNKNOWN")
        if predicted == expected:
            correct += 1
            tp[expected] += 1
        else:
            fp[predicted] += 1
            fn[expected] += 1
            mismatches.append({
                "id": case["id"],
                "expected": expected,
                "predicted": predicted,
                "error_message": (case.get("error_message") or "")[:120],
            })

    per_category: dict[str, dict[str, float]] = {}
    for cat in CATEGORIES:
        t = tp.get(cat, 0)
        f_pos = fp.get(cat, 0)
        f_neg = fn.get(cat, 0)
        precision = t / (t + f_pos) if (t + f_pos) > 0 else 0.0
        recall = t / (t + f_neg) if (t + f_neg) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        per_category[cat] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": t + f_neg,
        }

    precisions = [v["precision"] for v in per_category.values() if v["support"] > 0]
    recalls = [v["recall"] for v in per_category.values() if v["support"] > 0]
    f1s = [v["f1"] for v in per_category.values() if v["support"] > 0]

    latencies_sorted = sorted(latencies)
    p50_idx = int(len(latencies_sorted) * 0.5)
    p95_idx = min(int(len(latencies_sorted) * 0.95), len(latencies_sorted) - 1)

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "macro_precision": round(sum(precisions) / len(precisions), 4) if precisions else 0.0,
        "macro_recall": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "per_category": per_category,
        "mismatches": mismatches,
        "latency_ms": {
            "p50": round(latencies_sorted[p50_idx], 3),
            "p95": round(latencies_sorted[p95_idx], 3),
            "mean": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="TestLookup classification benchmark")
    parser.add_argument("--mode", default="rules", choices=["rules", "ml", "llm", "auto"])
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    cases = load_dataset()
    print(f"Loaded {len(cases)} labelled cases from {DATASET_PATH}")

    if args.mode == "rules":
        classify_fn = classify_via_rules
    else:
        print(f"Mode '{args.mode}' requires a running backend. Use --mode rules for offline eval.")
        sys.exit(1)

    print(f"Running classification benchmark (mode={args.mode})...")
    results = evaluate(cases, classify_fn)

    print(f"\nAccuracy: {results['accuracy']:.1%} ({results['correct']}/{results['total']})")
    print(f"Macro P/R/F1: {results['macro_precision']:.3f} / {results['macro_recall']:.3f} / {results['macro_f1']:.3f}")
    print(f"Latency (p50/p95): {results['latency_ms']['p50']:.2f}ms / {results['latency_ms']['p95']:.2f}ms")

    if results["mismatches"]:
        print(f"\n{len(results['mismatches'])} mismatches:")
        for m in results["mismatches"]:
            print(f"  {m['id']}: expected={m['expected']} predicted={m['predicted']} | {m['error_message']}")

    print("\nPer-category:")
    for cat, metrics in results["per_category"].items():
        print(f"  {cat:20s}  P={metrics['precision']:.3f}  R={metrics['recall']:.3f}  F1={metrics['f1']:.3f}  (n={metrics['support']})")

    output_path = args.output or f"benchmarks/results/classification_{args.mode}.json"
    import platform
    import datetime
    report = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": args.mode,
        "dataset_version": "1.0.0",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        **results,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    main()
