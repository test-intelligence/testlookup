"""W22b ACT-06 W-6: the single-row instance retention policy (service level).

D12: the instance default ships OFF; switching it on is ONLY the typed,
previewed enable — the preview lists every project whose effective ``enabled``
would flip (no policy row, or a NULL ``enabled``; an explicit ``false`` stays
off), hashes that set with the instance values, and writes nothing. Hermetic: a
statement-routing fake session; the affected SET itself is proved on real
Postgres by ``tests/integration/test_retention_inheritance_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy.dialects import postgresql  # noqa: E402

from app.services import retention_instance_service as inst_svc  # noqa: E402
from app.services.retention_instance_service import InstancePolicyError  # noqa: E402
from app.services.retention_service import RetentionValidationError  # noqa: E402
from app.services.retention_vocab import INHERITABLE_FIELDS, POLICY_ERROR_SENTENCES  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _InstDB:
    """Routes each statement by its compiled text; records every write."""

    def __init__(self, *, instance=None, affected=(), past=(), counts=(0,) * 7, users=()):
        self.instance = instance
        self.affected = list(affected)
        self.past = list(past)
        self.counts = tuple(counts)
        self.users = list(users)
        self.statements: list = []
        self.added: list = []
        self.flushes = 0
        self.commits = 0
        self.refreshed: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        s = str(stmt)
        if "FROM test_runs" in s:
            return _Rows(self.past)
        if "has_policy_row" in s:
            return _Rows(self.affected)
        if "FILTER (WHERE" in s:
            return _Rows([self.counts])
        if "FROM users" in s:
            return _Rows(self.users)
        if "instance_retention_policy" in s:
            return _Rows([self.instance] if self.instance is not None else [])
        raise AssertionError(f"Unrouted statement: {s[:200]}")

    def add(self, obj):
        self.added.append(obj)
        if type(obj).__name__ == "InstanceRetentionPolicy":
            self.instance = obj

    async def flush(self):
        self.flushes += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def commit(self):
        self.commits += 1


def _instance(**values):
    base = {field: None for field in INHERITABLE_FIELDS}
    base.update(enabled=False, enabled_at=None, enabled_by_id=None, updated_at=None, updated_by_user_id=None)
    base.update(values)
    return SimpleNamespace(**base)


def _affected_row(name, *, active=True, has_row=False, runs=365, audit=2555, window=2):
    return (uuid.uuid4(), name, active, has_row, 90, runs, 180, audit, 10, window)


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="admin-sentinel", full_name="Admin Sentinel")


def _audits(db):
    return [o for o in db.added if type(o).__name__ == "SettingsAuditLog"]


# ── Reads ────────────────────────────────────────────────────────────────────


async def test_a_missing_row_reads_as_the_code_defaults_off():
    values = await inst_svc.get_instance_policy(_InstDB(instance=None))
    assert values == inst_svc.InstanceRetentionValues()
    assert values.enabled is False and values.runs_days is None


async def test_a_raising_read_propagates():
    class _Broken(_InstDB):
        async def execute(self, stmt):
            raise ConnectionError("instance policy unreadable")

    with pytest.raises(ConnectionError):
        await inst_svc.get_instance_policy(_Broken())


async def test_inheriting_counts_is_one_statement_per_field_filter():
    db = _InstDB(counts=(4, 3, 2, 5, 6, 7, 1))
    counts = await inst_svc.inheriting_counts(db)
    assert counts == dict(zip(INHERITABLE_FIELDS, (4, 3, 2, 5, 6, 7, 1)))
    assert len(db.statements) == 1
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert sql.count("FILTER (WHERE project_retention_policies.id IS NULL OR") == 7
    assert "LEFT OUTER JOIN project_retention_policies" in sql


async def test_the_read_names_who_enabled_it_and_the_effective_values():
    who = uuid.uuid4()
    db = _InstDB(instance=_instance(enabled=True, runs_days=180, enabled_by_id=who), users=[("Ada Sentinel", "ada")])
    out = await inst_svc.instance_policy_read(db)
    assert out["enabled"] is True and out["runs_days"] == 180 and out["audit_days"] is None
    assert out["enabled_by_name"] == "Ada Sentinel"
    assert out["effective"]["runs_days"] == 180 and out["effective"]["audit_days"] == 2555
    assert out["code_defaults"]["purge_window_start_utc"] == 2
    assert db.added == [] and db.flushes == 0


# ── The enable preview (writes nothing) ──────────────────────────────────────


async def test_the_preview_selects_no_row_or_a_null_enabled_only():
    db = _InstDB(instance=_instance(), affected=[_affected_row("Payments sentinel")])
    await inst_svc.instance_enable_preview(db, now=datetime.now(timezone.utc))
    affected_sql = next(
        str(s.compile(dialect=postgresql.dialect())) for s in db.statements if "has_policy_row" in str(s)
    )
    where = affected_sql.split("WHERE", 1)[1]
    assert "project_retention_policies.id IS NULL OR project_retention_policies.enabled IS NULL" in where
    assert "is_active" not in where, "D-18: active or not, as the beat"
    assert "LEFT OUTER JOIN instance_retention_policy" in affected_sql
    runs_sql = next(str(s.compile(dialect=postgresql.dialect())) for s in db.statements if "FROM test_runs" in str(s))
    assert "GROUP BY test_runs.project_id" in runs_sql
    assert "make_interval" in runs_sql, "each project's OWN effective runs window"


async def test_the_preview_lists_every_affected_project_and_writes_nothing():
    now = datetime.now(timezone.utc)
    rows = [
        _affected_row("Alpha sentinel", has_row=True, runs=400, audit=380),
        _affected_row("Beta sentinel", active=False),
        _affected_row("Gamma sentinel", window=5),
    ]
    db = _InstDB(instance=_instance(), affected=rows, past=[(rows[0][0], 12), (rows[2][0], 3)])
    out = await inst_svc.instance_enable_preview(db, now=now)
    assert [item["name"] for item in out["items"]] == ["Alpha sentinel", "Beta sentinel", "Gamma sentinel"]
    assert (out["total"], out["truncated"], out["already_enabled"]) == (3, False, False)
    assert out["confirmation_phrase"] == "enable retention for 3 projects"
    alpha, beta, gamma = out["items"]
    assert alpha["has_policy_row"] is True and alpha["runs_past_window"] == 12
    assert alpha["effective_after"]["audit_days"] == 400, "the resolver's clamp: audit never below runs"
    assert beta["is_active"] is False and beta["runs_past_window"] == 0
    assert gamma["effective_after"]["purge_window_start_utc"] == 5
    assert out["preview_hash"] == inst_svc.preview_hash([r[0] for r in rows], inst_svc.InstanceRetentionValues())
    assert (db.added, db.flushes, db.commits) == ([], 0, 0)


async def test_the_phrase_for_one_project_is_singular():
    db = _InstDB(instance=_instance(), affected=[_affected_row("Solo sentinel")])
    out = await inst_svc.instance_enable_preview(db, now=datetime.now(timezone.utc))
    assert out["confirmation_phrase"] == "enable retention for 1 project"


async def test_the_hash_is_stable_and_moves_with_a_window_or_a_new_project():
    now = datetime.now(timezone.utc)
    rows = [_affected_row("Alpha sentinel"), _affected_row("Beta sentinel")]
    first = await inst_svc.instance_enable_preview(_InstDB(instance=_instance(), affected=rows), now=now)
    again = await inst_svc.instance_enable_preview(_InstDB(instance=_instance(), affected=list(reversed(rows))), now=now)
    assert first["preview_hash"] == again["preview_hash"], "the id set is sorted before hashing"
    window = await inst_svc.instance_enable_preview(
        _InstDB(instance=_instance(purge_window_start_utc=5), affected=rows), now=now,
    )
    assert window["preview_hash"] != first["preview_hash"]
    grown = await inst_svc.instance_enable_preview(
        _InstDB(instance=_instance(), affected=[*rows, _affected_row("Delta sentinel")]), now=now,
    )
    assert grown["preview_hash"] != first["preview_hash"]


async def test_the_preview_caps_its_list_but_always_counts_and_hashes_everything():
    rows = [_affected_row(f"Project sentinel {i:04d}") for i in range(501)]
    out = await inst_svc.instance_enable_preview(
        _InstDB(instance=_instance(), affected=rows), now=datetime.now(timezone.utc),
    )
    assert (len(out["items"]), out["total"], out["truncated"]) == (500, 501, True)
    assert out["preview_hash"] == inst_svc.preview_hash([r[0] for r in rows], inst_svc.InstanceRetentionValues())


async def test_an_enabled_default_previews_nothing():
    db = _InstDB(instance=_instance(enabled=True), affected=[_affected_row("Alpha sentinel")])
    out = await inst_svc.instance_enable_preview(db, now=datetime.now(timezone.utc))
    assert out["already_enabled"] is True and out["items"] == [] and out["total"] == 0
    assert not any("has_policy_row" in str(s) for s in db.statements)


# ── The typed enable ─────────────────────────────────────────────────────────


async def _preview_then_db(rows, **instance):
    now = datetime.now(timezone.utc)
    preview = await inst_svc.instance_enable_preview(_InstDB(instance=_instance(**instance), affected=rows), now=now)
    return preview, _InstDB(instance=_instance(**instance), affected=rows), now


async def test_the_enable_switches_it_on_and_audits_once():
    rows = [_affected_row("Alpha sentinel"), _affected_row("Beta sentinel")]
    preview, db, now = await _preview_then_db(rows, runs_days=180)
    actor = _actor()
    out = await inst_svc.enable_instance_policy(
        db, actor=actor, confirmation="enable retention for 2 projects", preview_hash=preview["preview_hash"], now=now,
    )
    row = db.instance
    assert out.enabled is True and row.enabled is True
    assert (row.enabled_at, row.enabled_by_id) == (now, actor.id)
    assert "FOR UPDATE" in str(db.statements[0].compile(dialect=postgresql.dialect())), "serialises two enables"
    (audit,) = _audits(db)
    assert (audit.setting_key, audit.action) == ("retention_instance_policy", "enabled")
    assert audit.changed_fields["count"] == 2
    assert audit.changed_fields["project_ids"] == [str(r[0]) for r in rows]
    assert audit.changed_fields["preview_hash"] == preview["preview_hash"]
    assert audit.changed_fields["values"]["runs_days"] == 180 and audit.changed_fields["values"]["enabled"] is True
    assert db.flushes == 1 and db.commits == 0, "stage-only: the route commits"


@pytest.mark.parametrize(
    ("case", "code", "status"),
    [
        ("already_on", "already_enabled", 409),
        ("stale", "preview_stale", 409),
        ("phrase", "confirmation_mismatch", 422),
    ],
)
async def test_the_enable_refusals_change_nothing(case, code, status):
    rows = [_affected_row("Alpha sentinel"), _affected_row("Beta sentinel")]
    preview, db, now = await _preview_then_db(rows)
    confirmation, digest = "enable retention for 2 projects", preview["preview_hash"]
    if case == "already_on":
        db.instance.enabled = True
    elif case == "stale":
        db.affected.append(_affected_row("Late sentinel"))   # a project appeared since the preview
    else:
        confirmation = "Enable retention for 2 projects"      # exact, case included
    with pytest.raises(InstancePolicyError) as err:
        await inst_svc.enable_instance_policy(db, actor=_actor(), confirmation=confirmation, preview_hash=digest, now=now)
    assert (err.value.code, err.value.status_code) == (code, status)
    assert err.value.message == POLICY_ERROR_SENTENCES[code]
    assert _audits(db) == [] and db.flushes == 0
    assert db.instance.enabled is (case == "already_on")


# ── The plain save ───────────────────────────────────────────────────────────


async def test_enabled_true_through_the_save_is_refused():
    db = _InstDB(instance=_instance())
    with pytest.raises(InstancePolicyError) as err:
        await inst_svc.upsert_instance_policy(db, actor=_actor(), values={}, enabled=True)
    assert (err.value.code, err.value.status_code) == ("enable_via_flow", 422)
    assert db.added == [] and db.statements == []


async def test_a_save_sets_and_clears_values_and_audits_it():
    db = _InstDB(instance=_instance(raw_events_days=60, artifacts_days=200))
    out = await inst_svc.upsert_instance_policy(
        db, actor=_actor(), values={"runs_days": 180, "keep_last_runs": None}, clear=["artifacts_days"],
    )
    assert (out.runs_days, out.artifacts_days, out.raw_events_days) == (180, None, 60)
    (audit,) = _audits(db)
    assert (audit.setting_key, audit.action) == ("retention_instance_policy", "updated")
    assert audit.changed_fields == {
        "changes": {"runs_days": {"old": None, "new": 180}, "artifacts_days": {"old": 200, "new": None}},
    }
    assert db.flushes == 1 and db.commits == 0


async def test_switching_it_off_is_a_plain_audited_save():
    db = _InstDB(instance=_instance(enabled=True))
    out = await inst_svc.upsert_instance_policy(db, actor=_actor(), values={}, enabled=False)
    assert out.enabled is False
    (audit,) = _audits(db)
    assert audit.action == "disabled" and audit.changed_fields["changes"]["enabled"] == {"old": True, "new": False}


@pytest.mark.parametrize(
    ("values", "clear", "code"),
    [
        ({}, ["enabled"], "unknown_inherit_field"),
        ({}, ["retention_reason"], "unknown_inherit_field"),
        ({"runs_days": 400}, ["runs_days"], "inherit_conflict"),
    ],
)
async def test_a_bad_clear_is_refused(values, clear, code):
    db = _InstDB(instance=_instance())
    with pytest.raises(InstancePolicyError) as err:
        await inst_svc.upsert_instance_policy(db, actor=_actor(), values=values, clear=clear)
    assert err.value.code == code and db.added == []


async def test_the_merged_audit_window_must_cover_the_runs_window():
    # runs 3000 with the audit left at its code default (2555): refused with
    # the project policy's own sentence.
    db = _InstDB(instance=_instance())
    with pytest.raises(RetentionValidationError, match="audit_days must be >= runs_days"):
        await inst_svc.upsert_instance_policy(db, actor=_actor(), values={"runs_days": 3000})
    assert db.added == []
