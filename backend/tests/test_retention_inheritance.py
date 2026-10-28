"""W22b ACT-06 W-1 / W-2: the per-field resolver and the inheriting writer.

The effective policy is ``project ▸ instance ▸ code default`` PER FIELD, for
the seven inheritable fields, resolved in ONE place
(``retention_service.get_effective_policy``). Hermetic: the statement-routing
``_FakeDB`` of ``test_retention_purge`` answers the instance read with its
``instance`` (none = the code defaults).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from app.services import retention_service as svc  # noqa: E402
from app.services.retention_service import (  # noqa: E402
    EffectiveRetentionPolicy,
    PolicyFieldError,
    RetentionValidationError,
)
from app.services.retention_vocab import INHERITABLE_FIELDS, POLICY_ERROR_SENTENCES  # noqa: E402
from tests.test_retention_purge import PROJECT_ID, _FakeDB  # noqa: E402

_INT_FIELDS = ("raw_events_days", "runs_days", "artifacts_days", "audit_days", "keep_last_runs", "purge_window_start_utc")
#: (project value, instance value, code default) per int field — each distinct.
_VALUES = {
    "raw_events_days": (30, 60, 90),
    "runs_days": (400, 500, 365),
    "artifacts_days": (40, 50, 180),
    "audit_days": (3000, 3100, 2555),
    "keep_last_runs": (3, 25, 10),
    "purge_window_start_utc": (5, 7, 2),
}


def _row(**values):
    """A policy row whose every inheritable column is NULL unless given."""
    base = {field: None for field in INHERITABLE_FIELDS}
    base.update(id=uuid.uuid4(), purge_paused_until=None, ai_versions_per_run=None, ai_versions_per_test=None)
    base.update(values)
    return SimpleNamespace(**base)


def _instance(**values):
    base = {field: None for field in INHERITABLE_FIELDS}
    base["enabled"] = False
    base.update(values)
    return SimpleNamespace(**base)


# ── The resolver (W-1) ───────────────────────────────────────────────────────


@pytest.mark.parametrize("field", _INT_FIELDS)
async def test_a_project_value_wins_over_the_instance_value(field):
    project, instance, _default = _VALUES[field]
    db = _FakeDB(policy=_row(**{field: project}), instance=_instance(**{field: instance}))
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert getattr(eff, field) == project
    assert eff.sources[field] == "project"
    assert eff.inherited[field] == instance, "what the field reads if it is reset to inherited"


@pytest.mark.parametrize("field", _INT_FIELDS)
async def test_a_null_project_column_inherits_the_instance_value(field):
    _project, instance, _default = _VALUES[field]
    db = _FakeDB(policy=_row(), instance=_instance(**{field: instance}))
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert getattr(eff, field) == instance
    assert eff.sources[field] == "instance"


@pytest.mark.parametrize("field", _INT_FIELDS)
async def test_an_instance_null_falls_back_to_the_code_default(field):
    _project, _instance_value, default = _VALUES[field]
    db = _FakeDB(policy=_row(), instance=_instance())
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert getattr(eff, field) == default
    assert eff.sources[field] == "default"
    assert svc.CODE_DEFAULTS[field] == default


@pytest.mark.parametrize(
    ("project", "instance", "expected", "source"),
    [
        (True, False, True, "project"),
        (False, True, False, "project"),       # an explicit false stays off
        (None, True, True, "instance"),
        (None, False, False, "default"),       # an instance that is off is no source
        (None, None, False, "default"),        # no instance row at all
    ],
)
async def test_the_sources_of_enabled(project, instance, expected, source):
    inst = _instance(enabled=instance) if instance is not None else None
    db = _FakeDB(policy=_row(enabled=project), instance=inst)
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert eff.enabled is expected
    assert eff.sources["enabled"] == source


async def test_no_row_inherits_an_enabled_instance_default_but_stays_source_default():
    db = _FakeDB(policy=None, instance=_instance(enabled=True, runs_days=180))
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert (eff.enabled, eff.runs_days) == (True, 180)
    assert eff.source == "default", "`source` keeps its meaning: custom = a project row exists"
    assert eff.sources["runs_days"] == "instance"


async def test_the_audit_window_is_never_shorter_than_the_runs_window():
    # Project runs 3000 (explicit) + the instance's audit 2555: a mix of
    # sources must not resolve audit < runs (D-9, the safe direction).
    db = _FakeDB(policy=_row(runs_days=3000), instance=_instance(audit_days=2555))
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert (eff.runs_days, eff.audit_days) == (3000, 3000)


async def test_a_raising_instance_read_propagates():
    class _Broken(_FakeDB):
        async def execute(self, stmt):
            if "instance_retention_policy" in str(stmt):
                raise ConnectionError("instance policy unreadable")
            return await super().execute(stmt)

    with pytest.raises(ConnectionError):
        await svc.get_effective_policy(_Broken(policy=_row(runs_days=400)), PROJECT_ID)


async def test_the_bare_dataclass_equals_the_no_row_no_instance_resolution():
    eff = await svc.get_effective_policy(_FakeDB(policy=None), PROJECT_ID)
    assert eff == EffectiveRetentionPolicy()
    assert set(eff.sources.values()) == {"default"}
    assert set(eff.sources) == set(INHERITABLE_FIELDS)
    assert eff.as_dict()["purge_window_start_utc"] == 2


async def test_a_pause_in_the_past_is_not_effective_and_a_future_one_is():
    now = datetime.now(timezone.utc)
    past, future = now - timedelta(hours=1), now + timedelta(days=2)
    eff = await svc.get_effective_policy(_FakeDB(policy=_row(purge_paused_until=past)), PROJECT_ID, now=now)
    assert eff.purge_paused_until is None
    eff = await svc.get_effective_policy(_FakeDB(policy=_row(purge_paused_until=future)), PROJECT_ID, now=now)
    assert eff.purge_paused_until == future


async def test_the_ai_caps_are_not_inherited():
    # D-12: an instance carries no caps; a NULL cap is the code default.
    db = _FakeDB(policy=_row(), instance=_instance(runs_days=500))
    eff = await svc.get_effective_policy(db, PROJECT_ID)
    assert (eff.ai_versions_per_run, eff.ai_versions_per_test) == (20, 10)
    assert "ai_versions_per_run" not in eff.sources


# ── The writer (W-2) ─────────────────────────────────────────────────────────


def _audit(db):
    return next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")


async def test_inherit_sets_the_column_null_and_records_the_source_change():
    row = _row(runs_days=400, audit_days=3000)
    db = _FakeDB(policy=row, instance=_instance(runs_days=500))
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, inherit=["runs_days"])
    assert row.runs_days is None
    assert (eff.runs_days, eff.sources["runs_days"]) == (500, "instance")
    assert _audit(db).changed_fields["changes"]["runs_days"] == {
        "old": 400, "new": 500, "source_old": "project", "source_new": "instance",
    }
    assert db.commits == 0


async def test_an_inherit_that_keeps_the_value_still_records_the_source():
    row = _row(runs_days=500)
    db = _FakeDB(policy=row, instance=_instance(runs_days=500))
    await svc.upsert_policy(db, project_id=PROJECT_ID, inherit=["runs_days"])
    assert _audit(db).changed_fields["changes"]["runs_days"] == {
        "old": 500, "new": 500, "source_old": "project", "source_new": "instance",
    }


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"inherit": ["runs_days", "retention_reason"]}, "unknown_inherit_field"),
        ({"inherit": ["ai_versions_per_run"]}, "unknown_inherit_field"),
        ({"inherit": ["runs_days"], "runs_days": 400}, "inherit_conflict"),
        ({"pause_days": 7, "resume": True}, "pause_conflict"),
    ],
)
async def test_the_three_refusals_stage_nothing(kwargs, code):
    db = _FakeDB(policy=_row(runs_days=400))
    with pytest.raises(PolicyFieldError) as err:
        await svc.upsert_policy(db, project_id=PROJECT_ID, **kwargs)
    assert err.value.code == code
    assert err.value.message == POLICY_ERROR_SENTENCES[code]
    assert not isinstance(err.value, RetentionValidationError), "the route maps it apart from the string 422"
    assert db.added == []


async def test_a_new_row_stores_only_the_fields_the_admin_sent():
    db = _FakeDB(policy=None, instance=_instance(raw_events_days=60))
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, runs_days=400, purge_window_start_utc=4)
    row = next(o for o in db.added if type(o).__name__ == "ProjectRetentionPolicy")
    assert (row.runs_days, row.purge_window_start_utc) == (400, 4)
    for field in ("enabled", "raw_events_days", "artifacts_days", "audit_days", "keep_last_runs"):
        assert getattr(row, field) is None, f"{field} must stay NULL (= inherit), not a frozen copy"
    assert (eff.raw_events_days, eff.sources["raw_events_days"]) == (60, "instance")
    assert eff.enabled is False and eff.source == "custom"
    assert _audit(db).changed_fields["changes"] == {"runs_days": 400, "purge_window_start_utc": 4}


async def test_pause_days_pauses_from_now_and_resume_clears_it():
    now = datetime.now(timezone.utc)
    row = _row(enabled=True)
    db = _FakeDB(policy=row)
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, pause_days=7, now=now)
    assert row.purge_paused_until == now + timedelta(days=7)
    assert eff.purge_paused_until == now + timedelta(days=7)
    assert _audit(db).changed_fields["changes"]["purge_paused_until"] == {
        "old": None, "new": (now + timedelta(days=7)).isoformat(),
    }

    db2 = _FakeDB(policy=row)
    eff = await svc.upsert_policy(db2, project_id=PROJECT_ID, resume=True, now=now)
    assert row.purge_paused_until is None and eff.purge_paused_until is None


async def test_the_merged_audit_check_sees_an_inherited_runs_window():
    # The project sets audit 400; resetting runs to the instance's 500 would
    # leave audit < runs — refused on the MERGED EFFECTIVE values.
    db = _FakeDB(policy=_row(runs_days=380, audit_days=400), instance=_instance(runs_days=500))
    with pytest.raises(RetentionValidationError, match="audit_days must be >= runs_days"):
        await svc.upsert_policy(db, project_id=PROJECT_ID, inherit=["runs_days"])
    assert db.added == []


async def test_an_instance_runs_window_is_checked_against_a_new_audit_value():
    db = _FakeDB(policy=_row(), instance=_instance(runs_days=600))
    with pytest.raises(RetentionValidationError, match="audit_days must be >= runs_days"):
        await svc.upsert_policy(db, project_id=PROJECT_ID, audit_days=500)


async def test_a_window_hour_out_of_bounds_is_refused():
    db = _FakeDB(policy=_row())
    with pytest.raises(RetentionValidationError, match="purge_window_start_utc"):
        await svc.upsert_policy(db, project_id=PROJECT_ID, purge_window_start_utc=24)
    assert db.added == []
