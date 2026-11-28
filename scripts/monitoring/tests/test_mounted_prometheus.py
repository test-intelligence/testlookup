"""Mounted file and expression checks use the same Prometheus configuration."""

from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_mounted_prometheus import InventoryError, REFERENCE, check  # noqa: E402


@pytest.fixture
def mounted(tmp_path):
    config = tmp_path / "prometheus.yml"
    shutil.copyfile(REFERENCE / "prometheus.yml", config)
    shutil.copyfile(REFERENCE / "backend-scrape.yml", tmp_path / "backend-scrape.yml")
    shutil.copytree(REFERENCE / "prometheus-rules", tmp_path / "rules")
    return config


def test_required_mounted_inventory_is_complete(mounted):
    groups, rules = check(mounted)
    assert groups >= 5 and rules >= 35
    (mounted.parent / "rules" / "testlookup-alerts.yml").unlink()
    with pytest.raises(InventoryError, match="1 missing"):
        check(mounted)


def test_missing_rule_in_mounted_file_fails_reference_parity(mounted):
    rule_file = mounted.parent / "rules" / "testlookup-alerts.yml"
    doc = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
    doc["groups"][0]["rules"].pop()
    rule_file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(InventoryError, match="rule groups differ"):
        check(mounted)


def test_infrastructure_opt_in_matches_generated_inventory(mounted):
    generated = mounted.parent / "generated-infrastructure.yml"
    generated.write_text(yaml.safe_dump({"groups": [{"name": "infrastructure-fixture", "rules": [
        {"record": "testlookup:infrastructure_expected_target", "expr": "vector(1)", "labels": {"instance": "node-a"}},
    ]}]}), encoding="utf-8")
    path = mounted.parent / "rules" / "infrastructure-contract.yml"
    with pytest.raises(InventoryError, match="1 missing"):
        check(mounted, infrastructure_rules=generated)
    shutil.copyfile(generated, path)
    with pytest.raises(InventoryError, match="1 unexpected"):
        check(mounted)
    assert check(mounted, infrastructure_rules=generated)[1] >= 35
    path.write_text(path.read_text(encoding="utf-8").replace("node-a", "node-b"), encoding="utf-8")
    with pytest.raises(InventoryError, match="rule groups differ"):
        check(mounted, infrastructure_rules=generated)


@pytest.mark.skipif(not os.environ.get("PROMTOOL_TEST_BINARY"), reason="requires the pinned CI promtool binary")
def test_native_config_check_rejects_a_bad_mounted_expression(mounted):
    rule_file = mounted.parent / "rules" / "testlookup-alerts.yml"
    doc = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
    doc["groups"][0]["rules"][0]["expr"] = "sum("
    rule_file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    result = subprocess.run([os.environ["PROMTOOL_TEST_BINARY"], "check", "config", str(mounted)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and "parse error" in result.stderr
