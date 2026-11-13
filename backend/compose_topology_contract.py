"""Small Docker Compose topology reader used by deployment contract tests.

This intentionally implements only the Compose surfaces the contract tests
read: service mappings, environment values, Celery worker commands and volume
mounts, plus Compose's merge tags (``!reset``, ``!override``).

Volumes merge the way Compose merges them, by mount target. Replacing the whole
list instead hid that ``volumes: []`` in an override file adds nothing: the
single-VM overlay's "production" services kept the development file's source
bind mounts (OSS review 2026-11-12, M-13).
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


class _Reset:
    """Compose's ``!reset``: drop the value inherited from earlier files."""

    def __init__(self, empty: Any) -> None:
        self.empty = empty


class _Override:
    """Compose's ``!override``: replace the inherited value instead of merging."""

    def __init__(self, value: Any) -> None:
        self.value = value


class ComposeLoader(yaml.SafeLoader):
    """A SafeLoader that also reads Compose's merge tags."""


def _construct_reset(loader: ComposeLoader, node: yaml.Node) -> _Reset:
    if isinstance(node, yaml.SequenceNode):
        return _Reset([])
    if isinstance(node, yaml.MappingNode):
        return _Reset({})
    return _Reset(None)


def _construct_override(loader: ComposeLoader, node: yaml.Node) -> _Override:
    if isinstance(node, yaml.SequenceNode):
        return _Override(loader.construct_sequence(node, deep=True))
    if isinstance(node, yaml.MappingNode):
        return _Override(loader.construct_mapping(node, deep=True))
    return _Override(loader.construct_scalar(node))


ComposeLoader.add_constructor("!reset", _construct_reset)
ComposeLoader.add_constructor("!override", _construct_override)


def _load_document(text: str) -> Any:
    loader = ComposeLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _resolve(value: Any) -> Any:
    """A document read on its own: ``!reset`` is empty, ``!override`` is its value."""
    if isinstance(value, _Reset):
        return deepcopy(value.empty)
    if isinstance(value, _Override):
        return _resolve(value.value)
    if isinstance(value, Mapping):
        return {key: _resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    return value


def load_compose_file(path: str | Path) -> dict[str, Any]:
    """One Compose file, read as Compose reads it on its own."""
    return _resolve(_load_document(Path(path).read_text(encoding="utf-8")) or {})


def _mount_target(volume: Any) -> str:
    if isinstance(volume, Mapping):
        return str(volume.get("target", ""))
    parts = str(volume).split(":")
    # "source:target[:mode]", or an anonymous volume that is only a target.
    if len(parts) >= 2 and not (len(parts[0]) == 1 and parts[0].isalpha()):
        return parts[1]
    return parts[-1] if len(parts) == 1 else str(volume)


def _merge_volumes(base: Any, override: list[Any]) -> list[Any]:
    merged = {_mount_target(v): deepcopy(v) for v in (base or [])}
    for volume in override:
        merged[_mount_target(volume)] = deepcopy(volume)
    return list(merged.values())


def _merge_mapping(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(value, _Reset):
            merged[key] = deepcopy(value.empty)
            continue
        if isinstance(value, _Override):
            merged[key] = _resolve(value.value)
            continue
        if key == "volumes" and isinstance(current, list) and isinstance(value, list):
            # Compose merges service volumes by mount target: an override can
            # add or replace a mount, never drop one (that needs !reset).
            merged[key] = _merge_volumes(current, _resolve(value))
        elif key == "environment" and current is not None and value is not None:
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
            merged[key] = deepcopy(_resolve(value))
    return merged


def load_compose_topology(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Load Compose files in CLI order and return their effective mapping."""
    merged: dict[str, Any] = {}
    for raw_path in paths:
        path = Path(raw_path)
        document = _load_document(path.read_text(encoding="utf-8")) or {}
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
