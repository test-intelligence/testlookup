"""Typed row models for a project import (W24 ADM-05 phase 1: settings + catalog).

An imported archive is attacker-controlled: every JSONL line of
``entities/<table>/part-NNNN.jsonl`` is validated by a Pydantic model built
from the CURRENT SQLAlchemy columns of that table, minus the columns the
entity's spec drops (``EntitySpec.drop``), BEFORE the first INSERT of the
import. ``extra="forbid"``: an unknown column, a wrong type or an over-long
string is an ``invalid_row`` (the caller names the entity, the line, the file
and the field NAME — never the value).

Type mapping (contract §1.4): ``UUID`` → ``uuid.UUID``; ``String(n)`` →
``str`` of at most ``n`` characters; ``Text`` → ``str``; ``Integer`` /
``BigInteger`` / ``SmallInteger`` → ``int``; ``Boolean`` → ``bool``;
``Float`` → ``float``; ``Numeric`` → ``Decimal``; ``DateTime`` → an AWARE
``datetime``; ``Date`` → ``date``; ``Time`` → ``time``; ``JSON`` / ``JSONB`` →
a JSON value; ``ARRAY(T)`` → ``list[T]``; a nullable column → ``Optional``.
No string may carry a NUL character (Postgres refuses one mid-transaction).

Schema evolution (contract §2.1): a column the archive does not carry is
UNSET (``model_dump(exclude_unset=True)`` omits it, so the INSERT takes the
server / Python default) unless the column is NOT NULL with no default — then
it is required. A column the archive carries but this deployment does not know
is refused (``extra="forbid"``; the manifest check names it first).

Pure: no session, no I/O, no logging (``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Annotated, Any, Optional, Protocol

import sqlalchemy as sa
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, StringConstraints, create_model
from sqlalchemy.sql.schema import Column

from app.models.postgres import Base

# The import fills these itself; an archive may omit them.
_IMPORT_FILLED = frozenset({"project_id"})
_JSON_MAX_DEPTH = 64


class _SpecLike(Protocol):
    """The two fields of ``project_export_entities.EntitySpec`` a row model needs."""

    @property
    def table(self) -> str: ...

    @property
    def drop(self) -> tuple[str, ...]: ...


class UnmappedColumnType(TypeError):
    """A column type the import has no row mapping for (a guard, not a runtime path)."""


def _no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL characters are not allowed")
    return value


def _json_clean(value: Any, depth: int = 0) -> Any:
    if depth > _JSON_MAX_DEPTH:
        raise ValueError("JSON value is nested too deeply")
    if isinstance(value, str):
        return _no_nul(value)
    if isinstance(value, list):
        for item in value:
            _json_clean(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _no_nul(key)
            _json_clean(item, depth + 1)
    return value


def _require_aware(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("a timestamp needs a UTC offset")
    return value


_Str = Annotated[str, AfterValidator(_no_nul)]
_Json = Annotated[JsonValue, AfterValidator(_json_clean)]
_AwareDatetime = Annotated[dt.datetime, AfterValidator(_require_aware)]


def python_type_for(column_type: sa.types.TypeEngine[Any]) -> Any:
    """The validated Python type for one SQLAlchemy column type (contract §1.4)."""
    if isinstance(column_type, sa.Uuid):
        return uuid.UUID
    if isinstance(column_type, sa.Enum):
        raise UnmappedColumnType("Enum columns are not exported")
    if isinstance(column_type, sa.Text):
        return _Str
    if isinstance(column_type, sa.String):
        if column_type.length:
            return Annotated[str, StringConstraints(max_length=column_type.length), AfterValidator(_no_nul)]
        return _Str
    if isinstance(column_type, sa.Boolean):
        return bool
    if isinstance(column_type, sa.Integer):
        return int
    if isinstance(column_type, sa.Float):
        return float
    if isinstance(column_type, sa.Numeric):
        return decimal.Decimal
    if isinstance(column_type, sa.DateTime):
        if not column_type.timezone:
            raise UnmappedColumnType("naive DateTime columns are not exported")
        return _AwareDatetime
    if isinstance(column_type, sa.Date):
        return dt.date
    if isinstance(column_type, sa.Time):
        return dt.time
    if isinstance(column_type, sa.ARRAY):
        return list[python_type_for(column_type.item_type)]  # type: ignore[misc]
    if isinstance(column_type, sa.JSON):
        return _Json
    raise UnmappedColumnType(f"no row mapping for {type(column_type).__name__}")


def _is_required(column: Column[Any]) -> bool:
    return (
        not column.nullable
        and column.server_default is None
        and column.default is None
        and column.name not in _IMPORT_FILLED
    )


def _field_for(column: Column[Any]) -> tuple[Any, Any]:
    py_type = python_type_for(column.type)
    if column.nullable:
        py_type = Optional[py_type]
    # Fields carry a prefixed Python name and the COLUMN name as alias, so no
    # column (``schema``, ``json``, ``model_name`` …) can shadow a BaseModel
    # attribute; validation, ``extra="forbid"``, errors and dumps all use the alias.
    if _is_required(column):
        return py_type, Field(alias=column.name)
    return py_type, Field(default=None, alias=column.name)


_CACHE: dict[tuple[str, tuple[str, ...]], type[BaseModel]] = {}


def row_model_for(spec: _SpecLike) -> type[BaseModel]:
    """The row model of one exported entity, built ONCE per (table, drop)."""
    key = (spec.table, tuple(spec.drop))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    table = Base.metadata.tables[spec.table]
    dropped = set(spec.drop)
    unknown = dropped - set(table.columns.keys())
    if unknown:
        raise KeyError(f"{spec.table}: drop names unknown columns {sorted(unknown)}")
    fields: dict[str, Any] = {
        f"c_{column.name}": _field_for(column) for column in table.columns if column.name not in dropped
    }
    model: type[BaseModel] = create_model(
        f"ImportRow_{spec.table}",
        __config__=ConfigDict(extra="forbid", strict=False, populate_by_name=False, protected_namespaces=()),
        **fields,
    )
    _CACHE[key] = model
    return model


def column_names(model: type[BaseModel]) -> frozenset[str]:
    """The column names (aliases) a row model accepts."""
    return frozenset(field.alias or name for name, field in model.model_fields.items())


def row_values(instance: BaseModel) -> dict[str, Any]:
    """``{column: value}`` of the columns the archive row actually carried."""
    return instance.model_dump(by_alias=True, exclude_unset=True)
