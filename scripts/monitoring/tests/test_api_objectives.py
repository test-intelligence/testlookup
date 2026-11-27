"""Contracts for optional objective loading, scoped dashboards and native fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "infra/monitoring/objectives/api-slos.yml"
RULES = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
ADOPTION = ROOT / "infra/monitoring/objectives/adoption.example.yml"
DASHBOARD = ROOT / "infra/monitoring/grafana/dashboards/testlookup-api-objectives.json"
FIXTURES = ROOT / "infra/monitoring/tests"


def test_objectives_are_opt_in_and_checked_in_policy_cannot_page():
    base = yaml.safe_load((ROOT / "infra/monitoring/prometheus.yml").read_text(encoding="utf-8"))
    loaded = {path.resolve() for pattern in base["rule_files"] for path in (ROOT / "infra/monitoring").glob(pattern)}
    assert SOURCE.resolve() not in loaded
    policy = yaml.safe_load(ADOPTION.read_text(encoding="utf-8"))
    records = [r for group in policy["groups"] for r in group["rules"]]
    assert {r["labels"]["objective"] for r in records} == {"api_availability", "api_latency"}
    assert all(r["expr"] == "vector(0)" for r in records)
    assert all(r["labels"]["owner"] == "UNASSIGNED" for r in records)
    assert all("api_sli" not in path.read_text(encoding="utf-8") for path in loaded)


def test_expensive_long_windows_have_bounded_evaluation_cadence():
    for group in RULES["groups"]:
        for record in group["rules"]:
            if "increase(" in record["expr"] and record.get("labels", {}).get("window") in {"3d", "30d"}:
                assert group["interval"] == "5m"
    fixture = yaml.safe_load((FIXTURES / "objective-burn-rates.yml").read_text(encoding="utf-8"))
    assert fixture["group_eval_order"] == [group["name"] for group in RULES["groups"]]
    for test in fixture["tests"]:
        for series in test["input_series"]:
            if series["series"].startswith("up{"):
                # up originates in scrape discovery, before application labels.
                assert "installation=" not in series["series"]
                assert "environment=" not in series["series"]
                assert "objective=" not in series["series"]


def test_native_thirty_day_boundary_fixture_uses_the_shipped_coverage_expression():
    coverage = next(
        r["expr"] for group in RULES["groups"] for r in group["rules"]
        if r.get("record") == "testlookup:api_sli_window_coverage" and r.get("labels", {}).get("window") == "30d"
    )
    fixture = yaml.safe_load((FIXTURES / "objective-window-coverage.yml").read_text(encoding="utf-8"))
    assert fixture["rule_files"] == []
    for test in fixture["tests"]:
        assert test["input_series"][0]["series"].startswith("testlookup:api_sli_five_minute_checkpoint{")
        for query in test["promql_expr_test"]:
            assert query["expr"] == f"({coverage}) >= 1"


def test_dashboard_queries_use_declared_records_and_one_exact_scope():
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    recorded = {r["record"] for g in RULES["groups"] for r in g["rules"] if "record" in r}
    variables = {v["name"]: v for v in dashboard["templating"]["list"]}
    for name in ("installation", "environment", "objective"):
        assert variables[name].get("includeAll") is False
        assert variables[name].get("multi") is False
    for name in ("cluster", "namespace"):
        assert variables[name]["type"] == "textbox"
        assert variables[name]["current"]["value"] == ""
    ids = []
    boxes = []
    for panel in dashboard["panels"]:
        ids.append(panel["id"])
        box = panel["gridPos"]
        for previous in boxes:
            assert (box["x"] + box["w"] <= previous["x"] or previous["x"] + previous["w"] <= box["x"]
                    or box["y"] + box["h"] <= previous["y"] or previous["y"] + previous["h"] <= box["y"])
        boxes.append(box)
        for target in panel.get("targets", []):
            query = target["expr"]
            assert not (set(re.findall(r"testlookup:[a-zA-Z0-9_]+", query)) - recorded)
            for name in ("installation", "environment", "cluster", "namespace", "objective"):
                assert f'{name}="$' + name + '"' in query
            assert "or vector(0)" not in query
            assert "clamp_min" not in query
            if panel["type"] in {"stat", "table"}:
                assert target["instant"] is True
                assert panel["fieldConfig"]["defaults"]["noValue"]
    assert len(ids) == len(set(ids))
    assert "Proposed" in dashboard["title"]
    assert "No data is unknown" in dashboard["panels"][0]["options"]["content"]


def test_source_metrics_exist_and_recording_scope_cannot_cross_installations():
    declared = (ROOT / "backend/app/core/metrics.py").read_text(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")
    for name in set(re.findall(r"\btestlookup_api_[a-zA-Z0-9_]+", source)):
        assert f'"{name}"' in declared
    for group in RULES["groups"]:
        for rule in group["rules"]:
            # Scope-preserving aggregations never combine two installations.
            for grouping in re.findall(r"(?:sum|count|max) by \(([^)]+)\)", rule["expr"]):
                assert {"installation", "environment", "cluster", "namespace", "objective"} <= set(grouping.split(", "))
