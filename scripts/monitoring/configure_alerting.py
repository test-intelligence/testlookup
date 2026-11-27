#!/usr/bin/env python3
"""Validate private receiver inputs and derive the opt-in Prometheus config.

This command never contacts a receiver and never prints input values. It
preserves the current base scrape/rule configuration, including authenticated
scrape include paths; the result is mounted at the original main-config path.
"""
from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}\Z")
SECRET_ENVIRONMENTS = {
    "incident_url": "ALERTMANAGER_INCIDENT_URL_SOURCE",
    "incident_token": "ALERTMANAGER_INCIDENT_TOKEN_SOURCE",
    "watchdog_url": "ALERTMANAGER_WATCHDOG_URL_SOURCE",
    "watchdog_token": "ALERTMANAGER_WATCHDOG_TOKEN_SOURCE",
}


class ConfigurationError(ValueError):
    """Safe error containing neither endpoint nor credential values."""


def validate_receiver_files(files: dict[str, Path]) -> None:
    for name in SECRET_ENVIRONMENTS:
        path = files[name]
        try:
            if not path.is_file() or path.stat().st_size > 8192:
                raise ConfigurationError(f"{name}: expected a nonempty private file of at most 8192 bytes")
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(f"{name}: cannot read UTF-8 secret file") from error
        if not value or any(character.isspace() or ord(character) < 32 for character in value):
            raise ConfigurationError(f"{name}: empty value or embedded whitespace is not allowed")
        if name.endswith("_url"):
            try:
                url = urlsplit(value)
                valid = (
                    url.scheme == "https" and bool(url.hostname)
                    and url.username is None and url.password is None
                    and not url.fragment and (url.port is None or 1 <= url.port <= 65535)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ConfigurationError(f"{name}: HTTPS URL without userinfo or fragment is required")


def render_prometheus(base: dict, *, installation: str, environment: str, owner: str) -> dict:
    for name, value in (("installation", installation), ("environment", environment), ("owner", owner)):
        if not IDENTITY.fullmatch(value):
            raise ConfigurationError(f"{name}: use 1-63 ASCII letters, digits, dots, underscores or hyphens")
    result = copy.deepcopy(base)
    if "alerting" in result or any(job.get("job_name") == "alertmanager" for job in result.get("scrape_configs", [])):
        raise ConfigurationError("base already contains alerting configuration; merge explicitly before generating")
    result.setdefault("global", {}).setdefault("external_labels", {}).update(
        installation=installation, environment=environment,
    )
    result["alerting"] = {
        "alert_relabel_configs": [{
            "source_labels": ["owner"], "regex": "", "target_label": "owner", "replacement": owner,
        }],
        "alertmanagers": [{
            "scheme": "http", "timeout": "10s", "api_version": "v2",
            "follow_redirects": False,
            "static_configs": [{"targets": ["alertmanager:9093"]}],
        }],
    }
    result.setdefault("scrape_configs", []).append({
        "job_name": "alertmanager", "scrape_interval": "15s",
        "static_configs": [{
            "targets": ["alertmanager:9093"],
            "labels": {"telemetry_profile": "compose", "telemetry_role": "alertmanager"},
        }],
    })
    result.setdefault("rule_files", []).append("alertmanager-contract.yml")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / "infra/monitoring/prometheus.yml")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--installation", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    try:
        files = {}
        for name, variable in SECRET_ENVIRONMENTS.items():
            if not os.environ.get(variable):
                raise ConfigurationError(f"{variable}: required private file path is missing")
            files[name] = Path(os.environ[variable])
        validate_receiver_files(files)
        if args.output.resolve() == args.base.resolve():
            raise ConfigurationError("output must differ from the tracked base configuration")
        rendered = render_prometheus(
            yaml.safe_load(args.base.read_text(encoding="utf-8")),
            installation=args.installation, environment=args.environment, owner=args.owner,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml.safe_dump(rendered, sort_keys=False), encoding="utf-8")
    except (ConfigurationError, OSError, yaml.YAMLError):
        # Do not include exception text: YAML parsers and paths can echo secrets.
        parser.exit(2, "Alert routing configuration failed; check private files, HTTPS URLs, identity fields and output path.\n")
    print("Validated receiver inputs and generated the opt-in Prometheus configuration; no notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
