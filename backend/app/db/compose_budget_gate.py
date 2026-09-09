"""One-shot Docker Compose connection-budget gate."""

from __future__ import annotations

import os
from collections.abc import Mapping


def calculate(environment: Mapping[str, str]) -> dict[str, int]:
    value = lambda name: int(environment[name])  # noqa: E731 - compact required lookup
    api = (
        value("PG_API_PROCESSES_PER_POD")
        * (value("PG_API_POOL_SIZE") + value("PG_API_MAX_OVERFLOW"))
    )
    workers = value("CELERY_CONCURRENCY") * (
        value("PG_WORKER_POOL_SIZE") + value("PG_WORKER_MAX_OVERFLOW")
    )
    children = value("CELERY_CHILDREN_CONCURRENCY") * (
        value("PG_WORKER_POOL_SIZE") + value("PG_WORKER_MAX_OVERFLOW")
    )
    migration = value("PG_FLEET_MIGRATION_CONNECTIONS")
    required = api + workers + children + migration
    declared = value("PG_FLEET_REQUIRED_CONNECTIONS")
    usable = (
        value("PG_FLEET_MAX_CONNECTIONS")
        - value("PG_FLEET_OPERATIONAL_RESERVE")
        - value("PG_FLEET_SUPERUSER_RESERVED_CONNECTIONS")
        - value("PG_FLEET_RESERVED_CONNECTIONS")
    )
    if required != declared:
        raise RuntimeError(
            f"Compose fleet requires {required} connections but declares {declared}"
        )
    if required > usable:
        raise RuntimeError(
            f"Compose fleet requires {required} connections but only {usable} are usable"
        )
    return {"api": api, "workers": workers, "children": children, "migration": migration,
            "required": required, "usable": usable}


def main() -> int:
    result = calculate(os.environ)
    print(
        "Compose PostgreSQL budget verified: "
        f"required={result['required']} usable={result['usable']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
