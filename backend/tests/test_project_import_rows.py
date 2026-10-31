"""W24 ADM-05 — the typed row models a project import validates every line with.

``row_model_for(spec)`` is built from the CURRENT SQLAlchemy columns minus the
spec's drops: each type mapping, ``extra="forbid"``, over-long strings, aware
datetimes, the nullable / required split that lets an older archive omit a
column, and a cache so each table's model is built once.
"""
from __future__ import annotations

import datetime as dt
import decimal
import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.models.postgres import Base
from app.services import project_import_rows as rows
from app.services.project_import_rows import column_names, python_type_for, row_model_for, row_values


@dataclass(frozen=True)
class _Spec:
    table: str
    drop: tuple[str, ...] = ()


def _errors(model, payload):
    with pytest.raises(ValidationError) as ei:
        model.model_validate(payload)
    return [(e["loc"], e["type"]) for e in ei.value.errors()]


def _cadence(**overrides):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "mode": "daily",
        "timezone": "UTC",
        "effective_from": now.isoformat(),
        "checked_through": now.isoformat(),
    }
    payload.update(overrides)
    return payload


def test_every_table_of_the_metadata_has_a_row_mapping():
    # No column type of today's schema is unmapped (Enum / naive DateTime /
    # LargeBinary would raise UnmappedColumnType at build time).
    for table in Base.metadata.tables:
        assert issubclass(row_model_for(_Spec(table)), rows.BaseModel)


def test_every_exported_entity_builds_with_its_drops():
    from app.services.project_export_entities import plan  # G-A (A0)

    for spec in plan():
        model = row_model_for(spec)
        assert not (set(spec.drop) & column_names(model)), spec.table


def test_the_model_is_built_once_per_table():
    assert row_model_for(_Spec("run_cadences")) is row_model_for(_Spec("run_cadences"))
    assert row_model_for(_Spec("run_cadences", ("cron",))) is not row_model_for(_Spec("run_cadences"))


def test_a_dropped_column_is_an_unknown_column():
    model = row_model_for(_Spec("releases", ("owner_user_id",)))
    payload = {"id": str(uuid.uuid4()), "name": "R1", "owner_user_id": str(uuid.uuid4())}
    assert _errors(model, payload) == [(("owner_user_id",), "extra_forbidden")]


def test_drop_naming_an_unknown_column_fails_at_build_time():
    with pytest.raises(KeyError):
        row_model_for(_Spec("releases", ("no_such_column",)))


def test_an_unknown_column_is_refused():
    model = row_model_for(_Spec("run_cadences"))
    assert _errors(model, _cadence(added_by_a_newer_release=1)) == [
        (("added_by_a_newer_release",), "extra_forbidden")
    ]


def test_uuid_text_becomes_a_uuid_and_garbage_is_refused():
    model = row_model_for(_Spec("run_cadences"))
    source_id = uuid.uuid4()
    assert row_values(model.model_validate(_cadence(id=str(source_id))))["id"] == source_id
    assert _errors(model, _cadence(id="not-a-uuid")) == [(("id",), "uuid_parsing")]


def test_an_over_long_string_is_refused():
    model = row_model_for(_Spec("run_cadences"))
    max_len = Base.metadata.tables["run_cadences"].c.paused_reason.type.length
    model.model_validate(_cadence(paused_reason="r" * max_len))
    assert _errors(model, _cadence(paused_reason="r" * (max_len + 1))) == [
        (("paused_reason",), "string_too_long")
    ]


def test_text_has_no_length_cap_but_refuses_a_nul():
    model = row_model_for(_Spec("managed_test_cases"))
    base = {"id": str(uuid.uuid4()), "title": "Checkout works"}
    model.model_validate({**base, "description": "d" * 100_000})
    assert _errors(model, {**base, "description": "a\x00b"}) == [(("description",), "value_error")]


def test_a_string_column_refuses_a_number():
    model = row_model_for(_Spec("managed_test_cases"))
    assert _errors(model, {"id": str(uuid.uuid4()), "title": 12345}) == [(("title",), "string_type")]


def test_integers_booleans_and_floats():
    model = row_model_for(_Spec("managed_test_cases"))
    base = {"id": str(uuid.uuid4()), "title": "T"}
    values = row_values(
        model.model_validate({**base, "version": 3, "is_automated": True, "faithfulness_score": 0.5})
    )
    assert (values["version"], values["is_automated"], values["faithfulness_score"]) == (3, True, 0.5)
    assert _errors(model, {**base, "version": "three"}) == [(("version",), "int_parsing")]
    assert _errors(model, {**base, "is_automated": "maybe"}) == [(("is_automated",), "bool_parsing")]
    assert _errors(model, {**base, "faithfulness_score": "high"}) == [(("faithfulness_score",), "float_parsing")]


def test_a_timestamp_must_be_aware():
    model = row_model_for(_Spec("run_cadences"))
    aware = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    values = row_values(model.model_validate(_cadence(effective_from=aware.isoformat())))
    assert values["effective_from"] == aware and values["effective_from"].utcoffset() is not None
    naive = dt.datetime.now().replace(microsecond=0).isoformat()
    assert _errors(model, _cadence(effective_from=naive)) == [(("effective_from",), "value_error")]


def test_time_and_date_columns():
    model = row_model_for(_Spec("run_cadences"))
    assert row_values(model.model_validate(_cadence(expected_by="09:30:00")))["expected_by"] == dt.time(9, 30)
    assert _errors(model, _cadence(expected_by="half past nine")) == [(("expected_by",), "time_parsing")]
    date_tables = [
        (t.name, c.name)
        for t in Base.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, sa.Date) and not isinstance(c.type, sa.DateTime)
    ]
    table, column = date_tables[0]
    field = row_model_for(_Spec(table)).model_fields[f"c_{column}"]
    assert dt.date in getattr(field.annotation, "__args__", (field.annotation,))


def test_json_columns_take_any_json_value_without_nul():
    model = row_model_for(_Spec("managed_test_cases"))
    base = {"id": str(uuid.uuid4()), "title": "T"}
    steps = [{"action": "open", "expected": ["a", 1, None, True, {"k": 2.5}]}]
    assert row_values(model.model_validate({**base, "steps": steps}))["steps"] == steps
    errors = _errors(model, {**base, "steps": [{"action": "a\x00"}]})
    assert errors and errors[0][0][0] == "steps"


def test_nullable_accepts_null_and_not_null_refuses_it():
    model = row_model_for(_Spec("managed_test_cases"))
    base = {"id": str(uuid.uuid4()), "title": "T"}
    assert row_values(model.model_validate({**base, "description": None}))["description"] is None
    # version is NOT NULL with a default: it may be omitted, never null.
    assert _errors(model, {**base, "version": None}) == [(("version",), "int_type")]


def test_an_omitted_column_is_unset_so_the_insert_takes_its_default():
    model = row_model_for(_Spec("managed_test_cases"))
    values = row_values(model.model_validate({"id": str(uuid.uuid4()), "title": "T"}))
    assert set(values) == {"id", "title"}  # status / version / created_at fall to their defaults


def test_a_not_null_column_without_a_default_is_required():
    model = row_model_for(_Spec("managed_test_cases"))
    assert _errors(model, {"id": str(uuid.uuid4())}) == [(("title",), "missing")]
    # project_id is NOT NULL without a default, but the import fills it.
    assert "project_id" in column_names(model)
    model.model_validate({"title": "T"})


def test_columns_that_shadow_basemodel_names_are_plain_columns():
    # Every field is aliased by its column name, so no column can shadow a
    # BaseModel attribute or the protected ``model_`` namespace.
    for table in Base.metadata.tables.values():
        model = row_model_for(_Spec(table.name))
        assert column_names(model) == set(table.columns.keys())


@pytest.mark.parametrize(
    ("column_type", "expected"),
    [
        (sa.ARRAY(sa.String(10)), list),
        (sa.Numeric(10, 2), decimal.Decimal),
    ],
    ids=["array", "numeric"],
)
def test_types_the_schema_does_not_use_yet_still_map(column_type, expected):
    mapped = python_type_for(column_type)
    assert getattr(mapped, "__origin__", mapped) is expected


@pytest.mark.parametrize(
    "column_type",
    [sa.LargeBinary(), sa.Enum("a", "b", name="e"), sa.DateTime(timezone=False)],
    ids=["bytea", "enum", "naive-datetime"],
)
def test_types_an_export_never_carries_fail_loudly(column_type):
    with pytest.raises(rows.UnmappedColumnType):
        python_type_for(column_type)
