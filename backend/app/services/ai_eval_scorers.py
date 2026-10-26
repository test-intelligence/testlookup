"""Pure scorers for a candidate REPLAY (W16 AGT-09 b).

A replay stores, per dataset item, what the REAL engine answered
(``ai_eval_runner``); these functions turn a stored output into an item status
and a run's metrics. They read nothing but their arguments: no session, no
model, no clock.

Three rules carry the whole design (contract §2.1, "absence is not health"):

* an item whose EXPECTED label is missing is ``unscored`` -- never ``correct``
  (the legacy ``compute_root_cause_metrics`` counts it correct: ``else:
  correct += 1``; that function is kept unchanged as the separately named
  *legacy label-agreement* metric and never shares a number with these);
* an output that lacks the scored field is ``incorrect``;
* metrics are computed over SCORED items only (``correct`` + ``incorrect``);
  with none scored every value is ``None`` and ``measured`` is ``False``.

The item's INPUT is read only for the root-cause sub-checks (the file / line
tokens of its stack trace). A dataset input that carries a prediction --
the golden classification items hold ``input.failure_category`` -- is never
read: the engine's output decides.

Commit-free and stage-only (``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.models.schemas import EvalMetric

#: The release recommendations a policy can answer (macro precision / recall).
RELEASE_RECOMMENDATIONS: tuple[str, ...] = ("GO", "CONDITIONAL_GO", "NO_GO")
#: Root-cause sub-checks, each compared with the item's own expected flag.
ROOT_CAUSE_SUB_CHECKS: tuple[str, ...] = ("mentions_file", "mentions_line", "actionable")

#: ``Name.ext:<line>`` -- the file token of a Java / JS / TS / Python frame
#: (``at Svc.run(Svc.java:142)``, ``tests/test_x.py:42``).
_FILE_LINE_RE = re.compile(r"([A-Za-z_][\w$-]*\.[A-Za-z][A-Za-z0-9]{0,7}):(\d+)")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _upper(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()


def _routing(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    routing = raw.get("_routing")
    return routing if isinstance(routing, Mapping) else {}


def project_output(task_type: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of an engine answer that are stored and scored (§2.1 table)."""
    if task_type == "classification":
        routing = _routing(raw)
        return {
            "failure_category": raw.get("failure_category"),
            "confidence_score": raw.get("confidence_score"),
            "root_cause_summary": _text(raw.get("root_cause_summary"))[:500],
            "mode_used": routing.get("mode_used", raw.get("mode_used")),
            "fallback_reason": routing.get("fallback_reason", raw.get("fallback_reason")),
        }
    if task_type == "root_cause":
        actions = raw.get("recommended_actions")
        return {
            "root_cause_summary": _text(raw.get("root_cause_summary")),
            "failure_category": raw.get("failure_category"),
            "recommended_actions": [str(a) for a in actions[:5]] if isinstance(actions, list) else [],
            "mode_used": _routing(raw).get("mode_used", raw.get("mode_used")),
        }
    if task_type == "duplicate_detection":
        ratio = raw.get("ratio")
        return {
            "ratio": round(float(ratio), 4) if isinstance(ratio, (int, float)) else None,
            "predicted_duplicate": raw.get("predicted_duplicate"),
            "detector_path": raw.get("detector_path", "title_ratio"),
        }
    if task_type == "release_decision":
        rules = raw.get("rule_evaluations")
        return {
            "recommendation": raw.get("recommendation"),
            "overall_result": raw.get("overall_result"),
            "effective_composite": raw.get("effective_composite"),
            "policy_level": raw.get("policy_level"),
            "policy_version": raw.get("policy_version"),
            "rule_results": [
                {"type": r.get("rule_type"), "passed": r.get("passed"), "action": r.get("action")}
                for r in (rules if isinstance(rules, list) else [])
                if isinstance(r, Mapping)
            ],
        }
    raise ValueError(f"unknown task type {task_type!r}")


def stack_file_tokens(stack_trace: Any) -> list[tuple[str, str]]:
    """``[(Name.ext, line)]`` of the frames in a stack trace, in order."""
    return [(m.group(1), m.group(2)) for m in _FILE_LINE_RE.finditer(_text(stack_trace))]


def _mentions_file(summary: str, tokens: Sequence[tuple[str, str]]) -> bool:
    lowered = summary.lower()
    return any(name.lower() in lowered for name, _ in tokens)


def _mentions_line(summary: str, tokens: Sequence[tuple[str, str]]) -> bool:
    for _, line in tokens:
        if re.search(rf"(?::|\bline\s+){line}\b", summary, flags=re.IGNORECASE):
            return True
    return False


def _verdict(correct: bool) -> str:
    return "correct" if correct else "incorrect"


def score_item(
    task_type: str,
    *,
    expected: Mapping[str, Any],
    output: Mapping[str, Any] | None,
    item_input: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """``(status, score)`` of one replayed item; status is an ``ITEM_STATUSES`` member.

    ``expected`` missing its label -> ``unscored``; ``output`` (or its scored
    field) missing -> ``incorrect``. Engine errors / fallbacks never reach this
    function: the runner records them as ``error`` before scoring.
    """
    expected = expected if isinstance(expected, Mapping) else {}
    out: Mapping[str, Any] = output if isinstance(output, Mapping) else {}
    if task_type == "classification":
        want = _upper(expected.get("failure_category"))
        if want is None:
            return "unscored", {"reason": "expected_label_missing"}
        got = _upper(out.get("failure_category"))
        return _verdict(got == want), {"expected": want, "predicted": got}
    if task_type == "root_cause":
        return _score_root_cause(expected, out, item_input)
    if task_type == "duplicate_detection":
        want_dup = expected.get("is_duplicate")
        if not isinstance(want_dup, bool):
            return "unscored", {"reason": "expected_label_missing"}
        raw_dup = out.get("predicted_duplicate")
        got_dup = raw_dup if isinstance(raw_dup, bool) else None
        return _verdict(got_dup is want_dup), {"expected": want_dup, "predicted": got_dup}
    if task_type == "release_decision":
        want_rec = _upper(expected.get("recommendation"))
        if want_rec is None:
            return "unscored", {"reason": "expected_label_missing"}
        got_rec = _upper(out.get("recommendation"))
        return _verdict(got_rec == want_rec), {"expected": want_rec, "predicted": got_rec}
    raise ValueError(f"unknown task type {task_type!r}")


def _score_root_cause(
    expected: Mapping[str, Any], out: Mapping[str, Any], item_input: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    want_cat = _upper(expected.get("category"))
    want_rc = expected.get("has_root_cause")
    if want_cat is None or not isinstance(want_rc, bool):
        # The legacy metric counted this item correct (``else: correct += 1``).
        return "unscored", {"reason": "expected_label_missing"}
    summary = out.get("root_cause_summary")
    got_cat = _upper(out.get("failure_category"))
    actions = out.get("recommended_actions")
    tokens = stack_file_tokens((item_input or {}).get("stack_trace"))
    text = summary if isinstance(summary, str) else ""
    score: dict[str, Any] = {
        "expected": want_cat,
        "predicted": got_cat,
        "has_root_cause": bool(text.strip()),
        "expected_has_root_cause": want_rc,
        "mentions_file": _mentions_file(text, tokens),
        "mentions_line": _mentions_line(text, tokens),
        "actionable": isinstance(actions, list) and any(str(a).strip() for a in actions),
    }
    for check in ROOT_CAUSE_SUB_CHECKS:
        if isinstance(expected.get(check), bool):
            score[f"expected_{check}"] = expected[check]
    if not isinstance(summary, str) or got_cat is None:
        return "incorrect", score
    return _verdict(score["has_root_cause"] == want_rc and got_cat == want_cat), score


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _metric(name: str, value: float | None, n: int, kind: str = "score") -> EvalMetric:
    measured = n > 0 and value is not None
    return EvalMetric(
        name=name,
        value=round(value, 4) if measured and value is not None else None,
        n=n,
        measured=measured,
        kind=kind,
    )


def _macro(scores: Sequence[Mapping[str, Any]], labels: Sequence[Any]) -> tuple[float, float]:
    """Macro precision / recall over ``labels`` (a label never seen scores 0)."""
    if not labels:
        return 0.0, 0.0
    precisions: list[float] = []
    recalls: list[float] = []
    for label in labels:
        tp = sum(1 for s in scores if s.get("predicted") == label and s.get("expected") == label)
        fp = sum(1 for s in scores if s.get("predicted") == label and s.get("expected") != label)
        fn = sum(1 for s in scores if s.get("expected") == label and s.get("predicted") != label)
        precisions.append(_ratio(tp, tp + fp))
        recalls.append(_ratio(tp, tp + fn))
    return sum(precisions) / len(precisions), sum(recalls) / len(recalls)


def _sub_check_metrics(scored: Sequence[Mapping[str, Any]]) -> list[EvalMetric]:
    """Root-cause sub-checks: the share of scored items whose observed check
    agrees with the item's own expected flag (items without the flag excluded)."""
    metrics: list[EvalMetric] = []
    for check in ROOT_CAUSE_SUB_CHECKS:
        judged = [s for s in scored if isinstance(s.get(f"expected_{check}"), bool)]
        agree = sum(1 for s in judged if bool(s.get(check)) is s[f"expected_{check}"])
        metrics.append(_metric(check, _ratio(agree, len(judged)) if judged else None, len(judged), "sub_check"))
    return metrics


def aggregate(task_type: str, statuses_and_scores: Sequence[tuple[str, Mapping[str, Any]]]) -> list[EvalMetric]:
    """Accuracy / precision / recall / F1 over SCORED items (``correct`` + ``incorrect``).

    * classification: macro precision / recall over the categories seen on
      either side; duplicate detection: binary (positive = duplicate); release
      decision: macro over GO / CONDITIONAL_GO / NO_GO; root cause: single-label,
      precision = recall = F1 = accuracy (the legacy convention).
    * F1 = 2PR / (P + R) of those P and R (the legacy convention).
    * ``n == 0`` -> every value ``None`` with ``measured: False`` (never 0 %).
    * root cause adds ``mentions_file`` / ``mentions_line`` / ``actionable`` as
      ``kind="sub_check"``.
    """
    scored = [score for status, score in statuses_and_scores if status in ("correct", "incorrect")]
    n = len(scored)
    correct = sum(1 for status, _ in statuses_and_scores if status == "correct")
    names = ("accuracy", "precision", "recall", "f1_score")
    if n == 0:
        metrics = [_metric(name, None, 0) for name in names]
        if task_type == "root_cause":
            metrics += [_metric(check, None, 0, "sub_check") for check in ROOT_CAUSE_SUB_CHECKS]
        return metrics
    accuracy = correct / n
    if task_type == "classification":
        labels: list[str] = sorted(
            {str(s["expected"]) for s in scored if s.get("expected")}
            | {str(s["predicted"]) for s in scored if s.get("predicted")}
        )
        precision, recall = _macro(scored, labels)
    elif task_type == "duplicate_detection":
        tp = sum(1 for s in scored if s.get("predicted") is True and s.get("expected") is True)
        fp = sum(1 for s in scored if s.get("predicted") is True and s.get("expected") is False)
        fn = sum(1 for s in scored if s.get("expected") is True and s.get("predicted") is not True)
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    elif task_type == "release_decision":
        precision, recall = _macro(scored, RELEASE_RECOMMENDATIONS)
    elif task_type == "root_cause":
        precision = recall = accuracy
    else:
        raise ValueError(f"unknown task type {task_type!r}")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    metrics = [_metric(name, value, n) for name, value in zip(names, (accuracy, precision, recall, f1))]
    if task_type == "root_cause":
        metrics += _sub_check_metrics(scored)
    return metrics


def metric_value(metrics: Sequence[EvalMetric], name: str) -> float | None:
    """The value of the ``kind="score"`` metric ``name`` (``None`` when unmeasured)."""
    for metric in metrics:
        if metric.name == name and metric.kind == "score":
            return metric.value
    return None


__all__ = [
    "RELEASE_RECOMMENDATIONS",
    "ROOT_CAUSE_SUB_CHECKS",
    "aggregate",
    "metric_value",
    "project_output",
    "score_item",
    "stack_file_tokens",
]
