"""
SLO-alert ratchet — keeps the Prometheus rules in sync with the Python
budgets.

Three places must agree on what "fast enough" means for each operation:

  1. ``app.services.performance_budgets.LATENCY_BUDGETS`` — the source
     of truth, used by the load harness and the live pytest smoke check.
  2. ``infra/monitoring/prometheus-rules/testlookup-alerts.yml`` —
     the production alerts.
  3. ``backend/scripts/load_test_concurrent.py::SCENARIOS`` — the load
     harness (already imports from #1, so no separate check needed).

This test parses the YAML alert file, finds every rule in the
``testlookup.slo`` group, extracts the threshold from its ``expr``
field, and asserts that the threshold matches the ``p95_ms`` of the
budget with the matching ``slo:`` label.

Drift modes this catches:
  * Alert threshold tightened/loosened without updating the budget
    (or vice versa).
  * New SLO budget added with no matching alert.
  * SLO alert added without registering an ``http_handler`` budget.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.performance_budgets import LATENCY_BUDGETS, get_budget

# Try to import yaml; the production deps already include it but if not,
# fall back to a regex-based extraction so this test works anywhere.
try:
    import yaml
    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


def _find_alerts_file() -> Path | None:
    """Walk upward from this file looking for ``infra/monitoring/prometheus-rules/testlookup-alerts.yml``.

    The file lives at the repo root under ``infra/`` which may or may
    not be mounted into the test container. When run on the host
    (or in CI with the full repo checked out) we find it; in a
    backend-only docker container we don't, and the SLO drift tests
    skip cleanly.
    """
    candidate_relpath = Path("infra") / "monitoring" / "prometheus-rules" / "testlookup-alerts.yml"
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        candidate = ancestor / candidate_relpath
        if candidate.exists():
            return candidate
    return None


_ALERTS_PATH = _find_alerts_file()

# Match the threshold at the end of an SLO expr:
#   ...rate(...)[5m]) ) > 0.25
_THRESHOLD_RE = re.compile(r">\s*([0-9]+\.?[0-9]*)\s*$", re.MULTILINE)


def _load_slo_rules() -> list[dict]:
    """Return the raw rule list from the testlookup.slo group."""
    if _ALERTS_PATH is None:
        pytest.skip(
            "infra/monitoring/prometheus-rules/testlookup-alerts.yml not found "
            "(infra/ not mounted in this container) — drift check skipped here. "
            "It still runs in CI / on the host where the full repo is checked out."
        )
    if not _HAS_YAML:
        pytest.skip("PyYAML not installed — cannot parse alert rules")

    data = yaml.safe_load(_ALERTS_PATH.read_text(encoding="utf-8"))
    for group in data.get("groups", []):
        if group.get("name") == "testlookup.slo":
            return group.get("rules", [])
    pytest.skip("testlookup.slo group not found in alert rules")
    return []  # unreachable, satisfies the type checker


def _extract_threshold_seconds(expr: str) -> float | None:
    """Pull the final ``> X`` numeric threshold out of a PromQL expression."""
    match = _THRESHOLD_RE.search(expr.strip())
    if not match:
        return None
    return float(match.group(1))


def _slo_label(rule: dict) -> str | None:
    return rule.get("labels", {}).get("slo")


# ── The tests ───────────────────────────────────────────────────────────────


def test_every_slo_alert_has_a_matching_budget() -> None:
    """Each ``slo:`` label in the alert YAML must point at a known
    operation in ``LATENCY_BUDGETS``. New alerts without budgets fail
    this; budgets without alerts fail the next test."""
    rules = _load_slo_rules()
    unknown: list[str] = []
    for rule in rules:
        slo = _slo_label(rule)
        if not slo:
            continue
        if get_budget(slo) is None:
            unknown.append(rule.get("alert", "<unnamed>"))
    assert not unknown, (
        "Alert rules reference SLO operations with no budget in "
        "LATENCY_BUDGETS — add the budget or fix the slo: label:\n  "
        + "\n  ".join(unknown)
    )


def test_alert_thresholds_match_budgets() -> None:
    """The threshold in each SLO rule's ``expr`` must equal the budget's
    ``p95_ms`` (converted to seconds, since Prometheus histograms are in
    seconds). Drift in either direction fails."""
    rules = _load_slo_rules()
    drift: list[str] = []
    for rule in rules:
        slo = _slo_label(rule)
        if not slo:
            continue
        budget = get_budget(slo)
        if budget is None:
            continue  # caught by the previous test
        threshold_s = _extract_threshold_seconds(rule["expr"])
        if threshold_s is None:
            drift.append(f"{rule['alert']}: could not parse threshold from expr")
            continue
        budget_s = budget.p95_ms / 1000.0
        if abs(threshold_s - budget_s) > 1e-6:
            drift.append(
                f"{rule['alert']} (slo={slo}): "
                f"alert threshold={threshold_s}s, budget p95={budget_s}s"
            )
    assert not drift, (
        "Alert thresholds drifted from LATENCY_BUDGETS — update one or the other:\n  "
        + "\n  ".join(drift)
    )


def test_every_http_budget_has_an_alert() -> None:
    """Every budget with an ``http_handler`` (i.e. observable on the HTTP
    histogram) must have at least one alert pointing at it. Catches the
    case where someone adds a budget but forgets the alert."""
    rules = _load_slo_rules()
    alerted = {_slo_label(rule) for rule in rules if _slo_label(rule)}
    missing = [
        b.operation
        for b in LATENCY_BUDGETS
        if b.http_handler and b.operation not in alerted
    ]
    assert not missing, (
        "Budgets with an http_handler exist but no Prometheus alert "
        "covers them — add a rule to testlookup-alerts.yml under "
        "the testlookup.slo group:\n  "
        + "\n  ".join(missing)
    )


# ── Dashboard drift ─────────────────────────────────────────────────────────


def _find_dashboard_file() -> Path | None:
    """Same upward-walking search as for the alerts file."""
    rel = (
        Path("infra") / "monitoring" / "grafana" / "dashboards"
        / "testlookup-overview.json"
    )
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        candidate = ancestor / rel
        if candidate.exists():
            return candidate
    return None


def test_dashboard_panels_cover_every_slo_budget() -> None:
    """The Grafana SLO row should reference every budget that has an
    ``http_handler``. Catches the case where someone adds a new budget
    + alert but forgets the dashboard panel.

    Match by the budget's ``http_handler`` appearing in any panel target's
    PromQL expression — that's the most stable identifier we can rely on
    from a JSON snapshot.
    """
    import json

    dashboard_path = _find_dashboard_file()
    if dashboard_path is None:
        pytest.skip("Grafana dashboard JSON not available in this environment")

    data = json.loads(dashboard_path.read_text(encoding="utf-8"))
    panels = data.get("panels", [])

    # Collect every PromQL expression across all panels' targets.
    all_exprs: list[str] = []
    for p in panels:
        for target in p.get("targets", []):
            expr = target.get("expr") or ""
            if expr:
                all_exprs.append(expr)

    missing: list[str] = []
    for budget in LATENCY_BUDGETS:
        if not budget.http_handler:
            continue
        # The handler shows up in the PromQL ``handler="..."`` label.
        needle = f'handler="{budget.http_handler}"'
        if not any(needle in expr for expr in all_exprs):
            missing.append(f"{budget.operation} ({budget.http_handler})")

    assert not missing, (
        "Budgets with http_handler are not visualized on the dashboard — "
        "add stat / timeseries panels referencing the handler in "
        "infra/monitoring/grafana/dashboards/testlookup-overview.json:\n  "
        + "\n  ".join(missing)
    )
