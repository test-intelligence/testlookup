"""Small Docker Compose topology reader used by deployment contract tests.

This intentionally implements only the Compose surfaces needed by the queue
contract: service mappings, environment values, and Celery worker commands.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import os
from pathlib import Path
import re
import shlex
from typing import Any

import yaml


_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-)([^}]*))?\}")


def _merge_mapping(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if key == "environment" and current is not None and value is not None:
            # Compose normalizes both list and mapping environment syntax to a
            # key/value mapping before applying an override.
            merged[key] = {
                **_environment_mapping(current),
                **_environment_mapping(value),
            }
        elif isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mapping(current, value)
        else:
            # Compose replaces scalar and sequence values supplied by a later
            # file. That is sufficient for command and the topology files'
            # environment declarations.
            merged[key] = deepcopy(value)
    return merged


def load_compose_topology(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Load Compose files in CLI order and return their effective mapping."""
    merged: dict[str, Any] = {}
    for raw_path in paths:
        path = Path(raw_path)
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(document, Mapping):
            raise ValueError(f"Compose document must be a mapping: {path}")
        merged = _merge_mapping(merged, document)
    return merged


def _environment_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): "" if item is None else str(item) for key, item in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            key, separator, setting = str(item).partition("=")
            result[key] = setting if separator else os.environ.get(key, "")
        return result
    raise ValueError("Compose service environment must be a mapping or list")


def _interpolate(value: str, environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment

    def replace(match: re.Match[str]) -> str:
        name, operator, default = match.groups()
        current = source.get(name)
        if current is not None and (operator != ":-" or current != ""):
            return current
        return default or ""

    return _INTERPOLATION.sub(replace, value)


def live_ingest_shard_count(
    topology: Mapping[str, Any], *, environment: Mapping[str, str] | None = None
) -> int:
    """Return the effective producer shard count, requiring services to agree."""
    observed: dict[str, int] = {}
    for name, service in topology.get("services", {}).items():
        values = _environment_mapping(service.get("environment"))
        if "LIVE_INGEST_SHARD_COUNT" in values:
            rendered = _interpolate(values["LIVE_INGEST_SHARD_COUNT"], environment)
            observed[str(name)] = int(rendered)
    if not observed:
        raise AssertionError("no service declares LIVE_INGEST_SHARD_COUNT")
    counts = set(observed.values())
    if len(counts) != 1:
        raise AssertionError(f"services disagree on LIVE_INGEST_SHARD_COUNT: {observed}")
    count = counts.pop()
    if count < 0:
        raise AssertionError(f"LIVE_INGEST_SHARD_COUNT must be non-negative: {observed}")
    return count


def celery_worker_queues(topology: Mapping[str, Any]) -> dict[str, set[str]]:
    """Map each Celery worker service to the queues passed via -Q/--queues."""
    subscriptions: dict[str, set[str]] = {}
    for name, service in topology.get("services", {}).items():
        command = service.get("command")
        if isinstance(command, list):
            tokens = [str(token) for token in command]
        elif isinstance(command, str):
            tokens = shlex.split(command)
        else:
            continue
        if "celery" not in tokens or "worker" not in tokens:
            continue
        queues = ""
        for index, token in enumerate(tokens):
            if token in {"-Q", "--queues"} and index + 1 < len(tokens):
                queues = tokens[index + 1]
                break
            if token.startswith("--queues="):
                queues = token.partition("=")[2]
                break
            if token.startswith("-Q") and token != "-Q":
                queues = token[2:]
                break
        subscriptions[str(name)] = {
            queue.strip() for queue in queues.split(",") if queue.strip()
        }
    return subscriptions


def required_queues(shard_count: int) -> set[str]:
    """Return every queue that a supported all-in-one topology must consume."""
    if shard_count < 0:
        raise ValueError("shard_count must be non-negative")
    queues = {"default", "critical", "ingestion", "ai_analysis", "agent_children"}
    queues.update(f"ingestion.shard.{index}" for index in range(shard_count))
    return queues
