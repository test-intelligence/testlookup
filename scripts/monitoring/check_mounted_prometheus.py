#!/usr/bin/env python3
"""Compare a reference Prometheus mount layout with its complete rule inventory."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

import yaml


REFERENCE = Path(__file__).resolve().parents[2] / "infra" / "monitoring"


class InventoryError(ValueError):
    pass


def read_yaml(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise InventoryError("an inventory document is empty or invalid")
    return document


def check(config: Path, *, alerting: bool = False, durable: bool = False, objectives: bool = False) -> tuple[int, int]:
    expected = {f"rules/{path.name}": path for path in (REFERENCE / "prometheus-rules").glob("*.yml")}
    if alerting:
        expected["alertmanager-contract.yml"] = REFERENCE / "alertmanager" / "telemetry-contract.yml"
    if durable:
        expected["rules/durable-telemetry.yml"] = REFERENCE / "otel-collector" / "telemetry-rules.yml"
    if objectives:
        expected["rules/api-slos.yml"] = REFERENCE / "objectives" / "api-slos.yml"
        expected["rules/slo-adoption.yml"] = REFERENCE / "objectives" / "adoption.example.yml"

    root = config.resolve().parent
    paths = []
    for entry in read_yaml(config).get("rule_files", []):
        pattern = Path(entry)
        if pattern.is_absolute() or ".." in pattern.parts:
            raise InventoryError("a rule file pattern leaves the reference mount layout")
        for match in root.glob(entry):
            if not match.is_file() or match.resolve() != match:
                raise InventoryError("rule mounts must be regular files inside the reference layout")
            paths.append(match.relative_to(root).as_posix())
    if Counter(paths) != Counter(expected.keys()):
        missing, extra = set(expected) - set(paths), set(paths) - set(expected)
        duplicates = len(paths) - len(set(paths))
        raise InventoryError(f"rule mount inventory differs, with {len(missing)} missing, {len(extra)} unexpected and {duplicates} duplicate files")

    groups = rules = 0
    for relative, source in expected.items():
        intended = read_yaml(source).get("groups", [])
        observed = read_yaml(root / relative).get("groups", [])
        if not intended or observed != intended:
            raise InventoryError(f"rule groups differ from the reference for {relative}")
        groups += len(observed)
        rules += sum(len(group["rules"]) for group in observed)
    if not groups or not rules:
        raise InventoryError("the reference rule inventory must contain actual rules")
    return groups, rules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--alerting", action="store_true")
    parser.add_argument("--durable", action="store_true")
    parser.add_argument("--objectives", action="store_true")
    args = parser.parse_args()
    try:
        groups, rules = check(args.config, alerting=args.alerting, durable=args.durable, objectives=args.objectives)
    except (InventoryError, OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        reason = str(error) if isinstance(error, InventoryError) else "the reference mount layout could not be read"
        print(f"Prometheus rule inventory failed: {reason}", file=sys.stderr)
        return 1
    print(f"Verified the mounted inventory of {groups} groups and {rules} Prometheus rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
