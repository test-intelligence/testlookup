"""
Generic ORM model serializer — eliminates per-service column-by-column conversion.

Handles UUID → str, datetime → ISO-8601, and Enum → value transparently.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Sequence


def serialize_model(
    instance: Any,
    *,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    """Convert a SQLAlchemy ORM instance to a JSON-safe dict.

    Args:
        instance: Any SQLAlchemy mapped object with ``__table__``.
        include:  If provided, only these column names are included.
        exclude:  Column names to skip (applied after *include*).

    Returns:
        A dict with column names as keys and JSON-serializable values.
    """
    exclude = exclude or set()
    data: dict[str, Any] = {}
    for col in instance.__table__.columns:
        name = col.name
        if include and name not in include:
            continue
        if name in exclude:
            continue
        data[name] = _serialize_value(getattr(instance, name))
    return data


def _serialize_value(value: Any) -> Any:
    """Convert a single value to a JSON-safe representation."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def serialize_models(
    instances: Sequence[Any],
    *,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Batch variant of ``serialize_model``."""
    return [serialize_model(i, include=include, exclude=exclude) for i in instances]
