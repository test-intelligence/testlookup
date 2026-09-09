#!/usr/bin/env python3
"""Fail when a rendered Kubernetes fleet can exhaust PostgreSQL headroom."""

from __future__ import annotations

import argparse
import math
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass(frozen=True)
class WorkloadBudget:
    name: str
    replicas: int
    surge: int
    processes_per_pod: int
    connections_per_process: int

    @property
    def connections(self) -> int:
        return (
            (self.replicas + self.surge)
            * self.processes_per_pod
            * self.connections_per_process
        )


def _env(container: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"]: str(item["value"])
        for item in container.get("env", [])
        if "value" in item
    }


def _surge(deployment: dict[str, Any], replicas: int) -> int:
    strategy = deployment["spec"].get("strategy")
    if not strategy:
        raise ValueError(
            f'{deployment["metadata"]["name"]}: DB workload must declare rollout strategy'
        )
    if strategy.get("type", "RollingUpdate") == "Recreate":
        return 0
    raw = strategy.get("rollingUpdate", {}).get("maxSurge")
    if raw is None:
        raise ValueError(
            f'{deployment["metadata"]["name"]}: maxSurge must be explicit'
        )
    if isinstance(raw, int):
        return raw
    value = str(raw)
    if value.endswith("%"):
        return math.ceil(replicas * int(value[:-1]) / 100)
    return int(value)


def _command_concurrency(container: dict[str, Any]) -> int | None:
    tokens = [str(token) for token in container.get("command", []) + container.get("args", [])]
    for index, token in enumerate(tokens):
        match = re.fullmatch(r"--concurrency=(\d+)", token)
        if match:
            return int(match.group(1))
        if token == "--concurrency" and index + 1 < len(tokens):
            return int(tokens[index + 1])
    return None


def calculate_budget(documents: Iterable[dict[str, Any]]) -> tuple[list[WorkloadBudget], dict[str, int]]:
    docs = [doc for doc in documents if doc]
    config = next(
        doc for doc in docs
        if doc.get("kind") == "ConfigMap" and doc.get("metadata", {}).get("name") == "testlookup-config"
    )["data"]
    limits = {
        "server_max": int(config["PG_FLEET_MAX_CONNECTIONS"]),
        "operational_reserve": int(config["PG_FLEET_OPERATIONAL_RESERVE"]),
        "superuser_reserved": int(config["PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS"]),
        "reserved": int(config["PG_FLEET_RESERVED_CONNECTIONS"]),
        "migration": int(config["PG_FLEET_MIGRATION_CONNECTIONS"]),
        "declared_required": int(config["PG_FLEET_REQUIRED_CONNECTIONS"]),
    }
    if limits["operational_reserve"] >= limits["server_max"]:
        raise ValueError("operational reserve must be below the server maximum")

    hpa_max = {
        doc["spec"]["scaleTargetRef"]["name"]: int(doc["spec"]["maxReplicas"])
        for doc in docs
        if doc.get("kind") == "HorizontalPodAutoscaler"
    }
    budgets: list[WorkloadBudget] = []
    for deployment in (doc for doc in docs if doc.get("kind") == "Deployment"):
        component = deployment.get("metadata", {}).get("labels", {}).get(
            "app.kubernetes.io/component"
        )
        if component not in {"api", "worker"}:
            continue
        name = deployment["metadata"]["name"]
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        env = _env(container)
        required = {
            "PG_PROCESS_ROLE",
            "PG_PROCESSES_PER_POD",
            "PG_POOL_SIZE",
            "PG_MAX_OVERFLOW",
        }
        missing = sorted(required - env.keys())
        if missing:
            raise ValueError(f"{name}: missing explicit DB settings: {', '.join(missing)}")
        if env["PG_PROCESS_ROLE"] != component:
            raise ValueError(f"{name}: PG_PROCESS_ROLE does not match component label")
        processes = int(env["PG_PROCESSES_PER_POD"])
        if component == "worker":
            command_concurrency = _command_concurrency(container)
            if command_concurrency != processes:
                raise ValueError(
                    f"{name}: PG_PROCESSES_PER_POD={processes} but Celery concurrency={command_concurrency}"
                )
        pool = int(env["PG_POOL_SIZE"])
        overflow = int(env["PG_MAX_OVERFLOW"])
        if processes < 1 or pool < 1 or overflow < 0:
            raise ValueError(f"{name}: DB process and pool values must be non-negative")
        replicas = hpa_max.get(name, int(deployment["spec"].get("replicas", 1)))
        budgets.append(
            WorkloadBudget(
                name=name,
                replicas=replicas,
                surge=_surge(deployment, replicas),
                processes_per_pod=processes,
                connections_per_process=pool + overflow,
            )
        )
    if not budgets:
        raise ValueError("rendered manifest contains no DB-owning API/worker workloads")
    return sorted(budgets, key=lambda item: item.name), limits


def validate(documents: Iterable[dict[str, Any]]) -> tuple[list[WorkloadBudget], dict[str, int]]:
    budgets, limits = calculate_budget(documents)
    app_connections = sum(item.connections for item in budgets)
    required = app_connections + limits["migration"]
    if required > limits["declared_required"]:
        raise ValueError(
            f"rendered fleet requires {required} connections, above "
            f"PG_FLEET_REQUIRED_CONNECTIONS={limits['declared_required']}"
        )
    usable = (
        limits["server_max"]
        - limits["operational_reserve"]
        - limits["superuser_reserved"]
        - limits["reserved"]
    )
    if required > usable:
        raise ValueError(
            f"fleet requires {required} connections but only {usable} are available "
            f"after reserving {limits['operational_reserve']} of {limits['server_max']}"
        )
    return budgets, limits


_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")


def _resolve(value: Any, environment: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name, default = match.groups()
        if name in environment and environment[name] != "":
            return environment[name]
        if default is not None:
            return default
        raise ValueError(f"missing Compose variable {name}")

    return _INTERPOLATION.sub(replace, str(value))


def _compose_env(service: dict[str, Any], environment: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    raw = service.get("environment", [])
    if isinstance(raw, dict):
        return {
            key: _resolve(value, environment)
            for key, value in raw.items()
            if key.startswith("PG_")
        }
    for item in raw:
        key, value = str(item).split("=", 1)
        if key.startswith("PG_"):
            result[key] = _resolve(value, environment)
    return result


def compose_environment(
    env_file: Path | None, process_environment: dict[str, str] | None = None
) -> dict[str, str]:
    """Load Compose's project .env, then apply shell variables as overrides."""
    result: dict[str, str] = {}
    if env_file and env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    result.update(os.environ if process_environment is None else process_environment)
    return result


def validate_compose(
    compose: dict[str, Any], environment: dict[str, str] | None = None
) -> tuple[list[WorkloadBudget], dict[str, int]]:
    environment = dict(os.environ if environment is None else environment)
    services = compose["services"]
    postgres_command = [
        _resolve(token, environment) for token in services["postgres"].get("command", [])
    ]
    cap_token = next(
        (token for token in postgres_command if token.startswith("max_connections=")), None
    )
    if cap_token is None:
        raise ValueError("Compose PostgreSQL must set max_connections explicitly")
    server_max = int(cap_token.split("=", 1)[1])

    budgets: list[WorkloadBudget] = []
    for name in ("backend", "worker", "worker-children"):
        env = _compose_env(services[name], environment)
        for required in (
            "PG_PROCESS_ROLE",
            "PG_PROCESSES_PER_POD",
            "PG_POOL_SIZE",
            "PG_MAX_OVERFLOW",
        ):
            if required not in env:
                raise ValueError(f"{name}: missing Compose DB setting {required}")
        if name != "backend":
            raw_command = services[name].get("command", "")
            if isinstance(raw_command, list):
                tokens = [_resolve(token, environment) for token in raw_command]
            else:
                tokens = shlex.split(_resolve(raw_command, environment))
            command_concurrency = _command_concurrency({"command": tokens})
            declared_processes = int(env["PG_PROCESSES_PER_POD"])
            if command_concurrency != declared_processes:
                raise ValueError(
                    f"{name}: PG_PROCESSES_PER_POD={declared_processes} but "
                    f"Celery concurrency={command_concurrency}"
                )
        budgets.append(
            WorkloadBudget(
                name=name,
                replicas=1,
                surge=0,
                processes_per_pod=int(env["PG_PROCESSES_PER_POD"]),
                connections_per_process=int(env["PG_POOL_SIZE"])
                + int(env["PG_MAX_OVERFLOW"]),
            )
        )
    backend_env = _compose_env(services["backend"], environment)
    limits = {
        "server_max": server_max,
        "operational_reserve": int(backend_env["PG_FLEET_OPERATIONAL_RESERVE"]),
        "superuser_reserved": int(
            backend_env["PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS"]
        ),
        "reserved": int(backend_env["PG_FLEET_RESERVED_CONNECTIONS"]),
        "migration": int(backend_env["PG_FLEET_MIGRATION_CONNECTIONS"]),
        "declared_required": int(backend_env["PG_FLEET_REQUIRED_CONNECTIONS"]),
    }
    required = sum(item.connections for item in budgets) + limits["migration"]
    if required != limits["declared_required"]:
        raise ValueError(
            f"Compose fleet requires {required} connections but declares "
            f"{limits['declared_required']}"
        )
    usable = (
        server_max
        - limits["operational_reserve"]
        - limits["superuser_reserved"]
        - limits["reserved"]
    )
    if required > usable:
        raise ValueError(
            f"Compose fleet requires {required} connections but only {usable} are usable"
        )
    return budgets, limits


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--compose", type=Path)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Compose environment file (defaults to .env beside --compose)",
    )
    args = parser.parse_args()
    if args.manifest:
        documents = list(yaml.safe_load_all(args.manifest.read_text(encoding="utf-8")))
        budgets, limits = validate(documents)
    else:
        env_file = args.env_file
        if env_file is None:
            candidate = args.compose.parent / ".env"
            env_file = candidate if candidate.exists() else None
        budgets, limits = validate_compose(
            yaml.safe_load(args.compose.read_text(encoding="utf-8")),
            compose_environment(env_file),
        )
    for item in budgets:
        print(
            f"{item.name}: ({item.replicas}+{item.surge}) pods x "
            f"{item.processes_per_pod} processes x {item.connections_per_process} = "
            f"{item.connections}"
        )
    app_connections = sum(item.connections for item in budgets)
    print(
        f"fleet: {app_connections} application + {limits['migration']} migration = "
        f"{app_connections + limits['migration']}; server={limits['server_max']}; "
        f"operational_reserve={limits['operational_reserve']}; "
        f"superuser_reserved={limits['superuser_reserved']}"
        f"; reserved={limits['reserved']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
