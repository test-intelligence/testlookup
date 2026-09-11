"""Alert rules that can actually fire, on every deployment target (re-audit H5).

Two defects kept TestLookupFinalizeStepFailing silent:

1. ``> 3`` in 30 minutes: on a quiet deployment (a few runs an hour) a step
   that fails on EVERY run never reaches four failures in the window.
2. A labelled counter has no series until its first ``inc()``, and that first
   scrape already reads 1, so ``increase()`` sees no change: the very first
   failure of a step was invisible even to ``> 0``. The step series are now
   created at import with value 0.

And there was no PrometheusRule under ``k8s/``, so on a cluster nothing fired
at all. promtool is not available offline, so the rules are validated here:
parsed, their metric names checked against the declarations, and the k8s
resource compared with the one source file.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERTS = REPO_ROOT / "infra" / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"
PROMETHEUS_RULE = REPO_ROOT / "k8s" / "monitoring" / "prometheusrule.yaml"
METRICS = REPO_ROOT / "backend" / "app" / "core" / "metrics.py"
PIPELINE = REPO_ROOT / "backend" / "app" / "services" / "ingestion_pipeline.py"

pytestmark = pytest.mark.skipif(
    not ALERTS.exists(), reason="infra/ not present (backend-only checkout)"
)


def _rules() -> list[dict]:
    doc = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return [rule for group in doc["groups"] for rule in group["rules"]]


def _duration_seconds(text: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    return sum(int(n) * units[u] for n, u in re.findall(r"(\d+)([smh])", text))


def _finalize_rule() -> dict:
    return next(r for r in _rules() if r.get("alert") == "TestLookupFinalizeStepFailing")


def test_one_failure_is_enough_to_fire() -> None:
    rule = _finalize_rule()
    expr = " ".join(rule["expr"].split())
    match = re.fullmatch(
        r"sum by \(step\) \(increase\(testlookup_finalize_step_failures_total"
        r"\[(\d+[smh])\]\)\) > (\d+)",
        expr,
    )
    assert match, f"unexpected expression shape: {expr}"
    window, threshold = _duration_seconds(match.group(1)), int(match.group(2))
    assert threshold == 0, "a step that fails on every run of a quiet deployment must alert"
    # `for:` smooths one scrape of jitter, and must be shorter than the window,
    # or a single failure's increase() drops back to 0 before `for:` elapses.
    pending = _duration_seconds(str(rule.get("for", "0m")))
    assert 0 < pending < window, (pending, window)


def test_every_step_series_exists_before_its_first_failure() -> None:
    from app.core import metrics

    # The names the code actually passes to the counter, read from the source.
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    called = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") in {"_run_isolated", "_run_isolated_step"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert len(called) >= 9, called
    assert set(metrics.FINALIZE_STEPS) == called

    samples = {
        sample.labels["step"]: sample.value
        for family in metrics.finalize_step_failures_total.collect()
        for sample in family.samples
        if sample.name.endswith("_total")
    }
    missing = called - set(samples)
    assert not missing, f"no series yet for {sorted(missing)}: their first failure is invisible"


def _declared_metrics() -> set[str]:
    tree = ast.parse(METRICS.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") in {"Counter", "Gauge", "Histogram", "Summary"}
                and node.args and isinstance(node.args[0], ast.Constant)):
            names.add(node.args[0].value)
    return names


def test_every_testlookup_metric_an_alert_reads_is_declared() -> None:
    declared = _declared_metrics()
    assert "testlookup_finalize_step_failures_total" in declared
    referenced = set()
    for rule in _rules():
        referenced |= set(re.findall(r"\btestlookup_[a-z0-9_]+", rule["expr"]))
    assert referenced, "no testlookup_ metrics referenced; the parse is broken"
    unknown = {
        name for name in referenced
        if re.sub(r"_(bucket|count|sum)$", "", name) not in declared
    }
    assert not unknown, f"alerts read metrics nothing declares: {sorted(unknown)}"


def test_the_kubernetes_rule_is_the_alerts_file() -> None:
    docs = [d for d in yaml.safe_load_all(PROMETHEUS_RULE.read_text(encoding="utf-8")) if d]
    assert len(docs) == 1
    resource = docs[0]
    assert resource["apiVersion"] == "monitoring.coreos.com/v1"
    assert resource["kind"] == "PrometheusRule"
    source = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    assert resource["spec"]["groups"] == source["groups"], (
        "k8s/monitoring/prometheusrule.yaml is stale: run "
        "`python scripts/render_prometheusrule.py`"
    )


def test_the_rule_is_opt_in_not_in_base() -> None:
    """A CR without its CRD fails the whole `kubectl apply -k` of base."""
    kustomization = yaml.safe_load(
        (REPO_ROOT / "k8s" / "monitoring" / "kustomization.yaml").read_text(encoding="utf-8")
    )
    # The rule and the PodMonitors that feed it (QA-B45-P5); both need the
    # Operator CRDs, so both stay out of base.
    assert kustomization["resources"] == ["prometheusrule.yaml", "podmonitors.yaml"]
    base = yaml.safe_load((REPO_ROOT / "k8s" / "base" / "kustomization.yaml").read_text(encoding="utf-8"))
    assert not any("monitoring" in str(r) or "prometheusrule" in str(r) for r in base["resources"])
