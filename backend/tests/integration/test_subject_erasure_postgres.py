"""W31 STRAT-12 — THE proof that nothing of an erased person remains outside the kept audit rows (§6.3 G-C).

A subject is seeded with NON-HEX sentinels (``zqxwsubj<tag>``, ``zqxw.subj.<tag>@example.org``,
"Zqxw Subjperson <tag>"; ``<tag>`` = 8 letters outside a-f, fresh per run, so a scan can never match a
uuid or another module's rows) in EVERY Postgres location of ``subject_data_inventory.INVENTORY``
(one row per FK column, generic rows built from ``Base.metadata``; the delete rows and the value
snapshots carry the sentinel, the project's reference rows neutral filler), plus Mongo, objects (a
subject export, a W24 project export listing them, a retention archive and a compliance pack) and Redis.
A bystander (``yvkqbystand<tag2>``) has rows beside them and @-mentions them. The erasure runs through
the ROUTE (the lock-out) and the RUNNER (three runs: the Postgres phase raises after the A deletes → a
full rollback; the object phase raises once → ``erasure_incomplete`` at phase ``postgres``; the third run
completes). Then (1) the sentinel scan over every row of every table (``t::text``) + ``auth_token_revocations``
— 0 outside the inventory's keep_audit tables, which are byte-equal to their snapshot; (2) Mongo; (3) the
objects; (4) Redis; (5) the subject's access token, refresh token and API key → 401; (6) the bystander intact
except the mention token; (7) re-entrancy (above); (8) the two new access-audit rows carry no sentinel and no
pseudonym; (9) every delete / rewrite location's job count equals the rows seeded there.

Fixture rules: ``TESTLOOKUP_POSTGRES_TEST_DSN`` (= the app's ``DATABASE_URL``, migrated to head 0253);
``dispose_engine_for_loop()`` before and after; ``reset_mongo_client()`` before and after; the local storage
provider in ``tmp_path``; every row / document / key / object this module seeded or caused is deleted by id
and the touched tables are back to their pre-test counts.
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
import pytest_asyncio
from sqlalchemy import insert, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_LETTERS = "ghijkmnopqrstuvwxyz"   # never a hex digit


def _tag(n: int = 8) -> str:
    return "".join(random.choice(_LETTERS) for _ in range(n))


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def sessions() -> Any:
    from app.db import postgres as app_postgres
    from app.db.mongo import reset_mongo_client

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    reset_mongo_client()
    engine = create_async_engine(dsn, pool_size=3, max_overflow=0)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        reset_mongo_client()
        await app_postgres.dispose_engine_for_loop()


# ── a generic row builder over Base.metadata ──────────────────────────────────

_CHECK_IN = re.compile(r"\(?\s*\(?(\w+)\)?(?:::\w+)?\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_CHECK_EQ = re.compile(r"\(?(\w+)\)?(?:::\w+)?\s*=\s*'([^']*)'", re.IGNORECASE)


def _check_literals(table: Any) -> dict[str, str]:
    """``col IN ('a', 'b')`` / ``col = 'a'`` CHECKs → the first literal a column may hold."""
    from sqlalchemy import CheckConstraint

    out: dict[str, str] = {}
    for constraint in table.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        sql = str(constraint.sqltext)
        for column, values in _CHECK_IN.findall(sql):
            literals = re.findall(r"'([^']*)'", values)
            if literals and column in table.c and column not in out:
                out[column] = literals[0]
        for column, value in _CHECK_EQ.findall(sql):
            if column in table.c and column not in out:
                out[column] = value
    return out


_HEX64 = "0" * 64
#: Multi-column CHECKs / partial uniques the generic filler cannot infer (read from pg_constraint): the values every
#: seeded row of the table starts from (a location's own values win).
_TABLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "sso_configurations": {"provider_type": "OIDC", "oidc_issuer": "https://idp.example.org/filler",
                           "oidc_client_id": "filler-client"},
    "agent_action_ledger": {"request_sha256": _HEX64},
    "decision_report_eval_cycles": {"corpus_sha256": _HEX64},
    "project_workflow_defaults": {"base": "offline", "workflow_id": "wf.filler"},
    "run_cadences": {"mode": "cron", "cron": "0 3 * * *"},
}
#: Tables whose rows must not share a parent (a partial unique per parent: one open review per case, one version
#: number per case, one active investigation per run, one decision per run).
_FRESH_PARENTS = {"test_case_reviews", "test_case_versions", "agent_investigations", "release_decisions",
                  "run_cadences"}


class Seeder:
    """Inserts one row into any ``Base.metadata`` table: the given values, then every NOT NULL column without
    a default filled by type (a CHECK's first literal when it has one; ``text`` for strings), a NOT NULL FK
    to ``users`` → ``fallback_user``, any other NOT NULL FK → one cached neutral parent row. Every row is
    recorded (table, primary key) so the teardown deletes exactly what was seeded, newest first."""

    def __init__(self, db: Any, *, now: datetime, fallback_user: Optional[uuid.UUID] = None) -> None:
        self.db, self.now, self.fallback_user = db, now, fallback_user
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.restores: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self._parents: dict[str, dict[str, Any]] = {}

    def _filler(self, column: Any, text_value: str) -> Any:
        kind = column.type
        if isinstance(kind, sqltypes.Enum):
            return kind.enums[0]
        if isinstance(kind, (postgresql.UUID, sqltypes.Uuid)):
            return uuid.uuid4()
        if isinstance(kind, sqltypes.Boolean):
            return False
        if isinstance(kind, sqltypes.Integer):
            return 1
        if isinstance(kind, (sqltypes.Numeric, sqltypes.Float)):
            return 1
        if isinstance(kind, sqltypes.DateTime):
            return self.now
        if isinstance(kind, sqltypes.Date):
            return self.now.date()
        if isinstance(kind, sqltypes.Interval):
            return timedelta(0)
        if isinstance(kind, sqltypes.ARRAY):
            return []
        if isinstance(kind, sqltypes.JSON):
            return {}
        if isinstance(kind, sqltypes.LargeBinary):
            return b"x"
        if isinstance(kind, postgresql.INET):
            return "127.0.0.1"
        value = f"{text_value} {uuid.uuid4().hex[:6]}"
        length = getattr(kind, "length", None)
        return value[:length] if length else value

    async def parent(self, table_name: str) -> dict[str, Any]:
        if table_name not in self._parents:
            self._parents[table_name] = await self.row(table_name, text="filler parent")
        return self._parents[table_name]

    async def row(self, table_name: str, values: Optional[dict[str, Any]] = None, *, text: str = "filler") -> dict:
        from app.models.postgres import Base

        table = Base.metadata.tables[table_name]
        literals = _check_literals(table)
        data = {**_TABLE_DEFAULTS.get(table_name, {}), **(values or {})}
        for column in table.columns:
            if column.name in data:
                continue
            foreign = list(column.foreign_keys)
            if column.primary_key and not foreign:
                if isinstance(column.type, sqltypes.Integer):
                    continue
                data[column.name] = self._filler(column, f"pk {table_name}")
                continue
            if foreign:
                if column.nullable and not column.primary_key:
                    continue
                target = foreign[0].column
                if target.table.name == "users":
                    data[column.name] = self.fallback_user
                elif table_name in _FRESH_PARENTS:
                    data[column.name] = (await self.row(target.table.name, text="filler parent"))[target.name]
                else:
                    data[column.name] = (await self.parent(target.table.name))[target.name]
                continue
            if column.nullable or column.default is not None or column.server_default is not None:
                continue
            data[column.name] = literals.get(column.name) or self._filler(column, text)
        try:
            async with self.db.begin_nested():
                inserted = (await self.db.execute(insert(table).values(**data).returning(table))).one()
        except Exception as exc:
            raise AssertionError(f"could not seed {table_name}: {type(exc).__name__}: {str(exc)[:400]}") from exc
        mapping = dict(inserted._mapping)
        self.created.append((table_name, {c.name: mapping[c.name] for c in table.primary_key.columns}))
        return mapping

    async def patch_singleton(self, table_name: str, values: dict[str, Any]) -> dict:
        """A singleton table (one pre-existing row, e.g. the instance retention policy): point its row at the
        subject, remembering the original values; :meth:`cleanup` restores them first."""
        from app.models.postgres import Base

        table = Base.metadata.tables[table_name]
        existing = (await self.db.execute(select(table).limit(1))).first()
        if existing is None:
            return await self.row(table_name, values)
        mapping = dict(existing._mapping)
        pk = {c.name: mapping[c.name] for c in table.primary_key.columns}
        self.restores.append((table_name, pk, {name: mapping[name] for name in values}))
        await self.db.execute(table.update().where(*[table.c[k] == v for k, v in pk.items()]).values(**values))
        return {**mapping, **values}

    async def cleanup(self) -> list[str]:
        from app.models.postgres import Base

        failures: list[str] = []
        for table_name, pk, original in self.restores:
            table = Base.metadata.tables[table_name]
            await self.db.execute(table.update().where(*[table.c[k] == v for k, v in pk.items()]).values(**original))
        for table_name, pk in reversed(self.created):
            table = Base.metadata.tables[table_name]
            where = [table.c[name] == value for name, value in pk.items()]
            try:
                async with self.db.begin_nested():
                    await self.db.execute(table.delete().where(*where))
            except Exception as exc:  # noqa: BLE001 - reported, never hides the next row
                failures.append(f"{table_name}: {type(exc).__name__}")
        await self.db.commit()
        return failures


# ── the world: three people, every inventory location, the other stores ──────

def _people() -> SimpleNamespace:
    """The subject (the per-run ``tag`` is in every sentinel), the bystander and the admin (``tag2``, never
    ``tag``: their rows must survive the scan)."""
    tag, tag2 = _tag(), _tag()
    return SimpleNamespace(
        tag=tag,
        S=SimpleNamespace(username=f"zqxwsubj{tag}", email=f"zqxw.subj.{tag}@example.org",
                          full_name=f"Zqxw Subjperson {tag}"),
        B=SimpleNamespace(username=f"yvkqbystand{tag2}", email=f"yvkq.bystand.{tag2}@example.org",
                          full_name=f"Yvkq Bystand {tag2}"),
        A=SimpleNamespace(username=f"yvkqadmin{tag2}", email=f"yvkq.admin.{tag2}@example.org",
                          full_name=f"Yvkq Admin {tag2}"),
    )


#: The B-list rows: (location key, the values that name the subject) — ``S`` is the subject, ``B`` the bystander.
def _b_rows(ids: SimpleNamespace, S: SimpleNamespace, B: SimpleNamespace) -> list[tuple[str, str, dict]]:
    return [
        ("project_insights.dismissed_by_name", "project_insights",
         {"dismissed_by_id": ids.subject, "dismissed_by_name": S.full_name}),
        ("report_share_links.created_by_name", "report_share_links",
         {"created_by_id": ids.subject, "created_by_name": S.full_name}),
        ("agent_investigations.cancelled_by", "agent_investigations", {"cancelled_by": S.username}),
        ("agent_investigations.cancelled_by", "agent_investigations", {"cancelled_by": S.email}),
        ("agent_pipeline_runs.error", "agent_pipeline_runs", {"error": f"cancelled: by {S.email}"}),
        ("test_case_comments.content", "test_case_comments",
         {"author_id": ids.bystander, "content": f"hey @{S.username}, please look (from @{B.username})"}),
        ("webhook_deliveries.event_payload", "webhook_deliveries",
         {"event_payload": {"event": "defect.create_requested", "requested_by": S.username}}),
        ("notification_logs.title", "notification_logs",
         {"user_id": ids.bystander, "title": f"{S.full_name} mentioned you on Login", "body": f"see @{B.username}"}),
        ("notification_logs.body", "notification_logs",
         {"user_id": ids.bystander, "title": "Weekly flaky review",
          "body": f"owner: {S.full_name} — ping @{S.username}"}),
    ]


def _audit_values(key: str, ids: SimpleNamespace, S: SimpleNamespace) -> dict[str, Any]:
    """The kept audit / legal rows name the subject exactly as the real writers do."""
    return {
        "access_audit_logs.actor_user_id": {"actor_user_id": ids.subject, "actor_name": S.username},
        "access_audit_logs.target_user_id": {"target_user_id": ids.subject, "actor_user_id": ids.bystander},
        "identity_events.user_id": {"user_id": ids.subject, "actor_name": S.username, "detail": {"email": S.email}},
        "identity_events.actor_id": {"actor_id": ids.subject, "actor_name": S.username},
        "settings_audit_log.actor_id": {"actor_id": ids.subject, "actor_name": S.username},
        "test_case_audit_logs.actor_id": {"actor_id": ids.subject, "actor_name": S.full_name},
        "project_activity_events.actor_id": {"actor_id": ids.subject, "actor_name": S.full_name,
                                             "actor_type": "user"},
        "release_decisions.overridden_by": {"overridden_by": ids.subject,
                                            "override_audit": [{"actor_name": S.username, "reason": "ship"}]},
        "retention_holds.placed_by_id": {"placed_by_id": ids.subject, "placed_by_name": S.full_name},
        "retention_holds.released_by_id": {"released_by_id": ids.subject, "released_by_name": S.full_name},
    }.get(key, {})


async def _location_values(key: str, seeder: Seeder) -> dict[str, Any]:
    """Values a reference row needs beyond its user column (a multi-column CHECK, a partial unique)."""
    if key == "workflow_definitions.eval_regression_accepted_by":
        from app.models.postgres import Base

        gate = list(Base.metadata.tables["workflow_definitions"].c.eval_gate_run_id.foreign_keys)[0].column
        return {"eval_regression_accepted": True, "eval_verdict": "fail", "eval_regression_reason": "accepted",
                "eval_regression_accepted_at": seeder.now, "eval_coverage": 0.5, "evaluated_at": seeder.now,
                "eval_gate_run_id": (await seeder.parent(gate.table.name))[gate.name]}
    if key.startswith("compliance_signing_keys."):
        return {"retired_at": seeder.now}               # never a second ACTIVE key (uq_compliance_signing_keys_active)
    if key.startswith("release_gate_policies."):
        return {"project_id": (await seeder.row("projects", text="filler parent"))["id"]}   # not a system policy
    if key == "dismissed_duplicate_pairs.dismissed_by_user_id":
        from app.models.postgres import Base

        table = Base.metadata.tables["dismissed_duplicate_pairs"]
        target = list(table.c.case_a_id.foreign_keys)[0].column
        pair = sorted([(await seeder.row(target.table.name, text="filler parent"))[target.name] for _ in range(2)],
                      key=str)
        return {"case_a_id": pair[0], "case_b_id": pair[1]}
    return {}


async def _seed_people(seeder: Seeder, P: SimpleNamespace) -> SimpleNamespace:
    from app.core.security import get_password_hash

    hashed = get_password_hash("zqxw-" + _tag(12))
    ids = SimpleNamespace()
    for attr, person, role in (("admin", P.A, "ADMIN"), ("subject", P.S, "TESTER"), ("bystander", P.B, "TESTER")):
        row = await seeder.row("users", {
            "id": uuid.uuid4(), "username": person.username, "email": person.email, "full_name": person.full_name,
            "hashed_password": hashed, "role": role, "is_active": True, "avatar_color": "blue",
        })
        setattr(ids, attr, row["id"])
    seeder.fallback_user = ids.bystander
    return ids


async def _seed_postgres(seeder: Seeder, ids: SimpleNamespace, P: SimpleNamespace) -> SimpleNamespace:
    """One row per Postgres location (the delete rows and the kept audit rows carry the sentinel; the project's
    reference rows neutral filler) + the B rows + the bystander's own rows. Returns the expected per-location
    counts and the rows the assertions follow."""
    from app.services.subject_data_inventory import INVENTORY

    S, B = P.S, P.B
    expected: dict[str, int] = {}
    reference_rows: list[tuple[str, dict]] = []
    covered = {"users.*", "auth_token_revocations.user_id", "subject_rights_jobs.subject_user_id",
               "subject_rights_jobs.requested_by_id"}
    for location in INVENTORY:
        if location.store != "postgres" or location.key in covered or location.erase in ("out_of_scope",):
            continue
        if location.erase == "delete":
            values: dict[str, Any] = {}
            if location.match == "fk":
                values[location.column] = ids.subject
            if location.key == "saved_views.user_id":
                values["is_shared"] = False
            if location.key == "secret_refs.key_name":
                values.update(scope="user_totp", key_name=str(ids.subject), encrypted_value=S.full_name)
            if location.table == "user_invitations":
                values["email"] = S.email.upper() if location.match == "value" else S.email
            row = await seeder.row(location.table, values, text=S.full_name)
            if location.key == "chat_sessions.user_id":
                await seeder.row("chat_messages", {"session_id": row["id"], "content": S.full_name}, text=S.full_name)
            expected[location.key] = expected.get(location.key, 0) + 1
        elif location.erase == "keep_audit" and location.match == "fk":
            await seeder.row(location.table, {location.column: ids.subject, **_audit_values(location.key, ids, S)},
                             text=S.full_name)
        elif location.erase == "reference":
            if location.table == "instance_retention_policy":   # a singleton: its one row points at the subject
                await seeder.patch_singleton(location.table, {location.column: ids.subject})
                continue
            extra = await _location_values(location.key, seeder)
            row = await seeder.row(location.table, {location.column: ids.subject, **extra}, text="filler")
            reference_rows.append((location.table, row))
    for key, table, values in _b_rows(ids, S, B):
        row = await seeder.row(table, values, text="filler")
        expected[key] = expected.get(key, 0) + 1
        if key == "test_case_comments.content":
            mention = row
    # a SHARED saved view is the project's record: kept (neutral text), its owner reads "Erased user"
    shared = await seeder.row("saved_views", {"user_id": ids.subject, "is_shared": True}, text="filler")
    # the bystander's own rows beside the subject's (each must survive untouched)
    bystander_rows = [
        (table, await seeder.row(table, {"user_id": ids.bystander}, text=B.full_name))
        for table in ("api_keys", "saved_views", "user_ui_dismissals", "notification_preferences", "chat_sessions")
    ]
    return SimpleNamespace(expected=expected, reference_rows=reference_rows, mention=mention, shared=shared,
                           bystander_rows=bystander_rows)


def _zip(members: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value if isinstance(value, str) else json.dumps(value))
    return buffer.getvalue()


async def _seed_objects(seeder: Seeder, storage: Any, ids: SimpleNamespace, P: SimpleNamespace,
                        now: datetime) -> SimpleNamespace:
    """The subject's own export, a W24 project export that lists them and one that does not, a retention
    archive and a compliance pack naming them (both kept, Art. 17(3)(e))."""
    from app.services.compliance_pack_service import COMPLIANCE_BUCKET

    S, B = P.S, P.B
    day = now.strftime("%Y/%m/%d")
    export_id = uuid.uuid4()
    keys = SimpleNamespace(subject_export=f"subject-exports/{day}/{export_id}.zip",
                           listing=f"project-exports/{day}/{uuid.uuid4()}/{uuid.uuid4()}.zip",
                           other=f"project-exports/{day}/{uuid.uuid4()}/{uuid.uuid4()}.zip",
                           retention=f"exports/{day}/{uuid.uuid4()}.zip", pack=f"compliance/{uuid.uuid4()}.zip")
    directory = "\n".join(json.dumps(r) for r in (
        {"id": str(ids.subject), "username": S.username, "full_name": S.full_name},
        {"id": str(ids.bystander), "username": B.username, "full_name": B.full_name}))
    contents = {
        keys.subject_export: _zip({"profile.json": {"username": S.username, "email": S.email}}),
        keys.listing: _zip({"directory/users.jsonl": directory + "\n", "manifest.json": {"format_version": 1}}),
        keys.other: _zip({"directory/users.jsonl": json.dumps({"id": str(ids.bystander), "username": B.username})}),
        keys.retention: _zip({"manifest.json": {"requested_by": {"name": S.full_name}}}),
        keys.pack: _zip({"audit/access.jsonl": json.dumps({"actor_name": S.username})}),
    }
    for key, blob in contents.items():
        await storage.put_object(key, blob, content_type="application/zip", bucket=COMPLIANCE_BUCKET)
    export_job = await seeder.row("subject_rights_jobs", {
        "id": export_id, "kind": "export", "status": "succeeded", "source": "self", "phase": None,
        "subject_user_id": ids.subject, "requested_by_id": ids.subject, "storage_key": keys.subject_export,
        "counts": {}, "attempts": 1, "finished_at": now, "expires_at": now + timedelta(hours=72),
    })
    listing_job = await seeder.row("project_data_jobs", {
        "kind": "export", "status": "succeeded", "storage_key": keys.listing, "requested_by_id": ids.bystander,
        "counts": {}, "options": {}, "finished_at": now, "expires_at": now + timedelta(hours=72)})
    other_job = await seeder.row("project_data_jobs", {
        "kind": "export", "status": "succeeded", "storage_key": keys.other, "requested_by_id": ids.bystander,
        "counts": {}, "options": {}, "finished_at": now, "expires_at": now + timedelta(hours=72)})
    return SimpleNamespace(keys=keys, export_job=export_job["id"], listing_job=listing_job["id"],
                           other_job=other_job["id"], bucket=COMPLIANCE_BUCKET)


async def _seed_mongo(ids: SimpleNamespace, probe: str) -> list[str]:
    """One document per collection the subject's activity can reach — ids only, as every writer does (E8)."""
    from app.db.mongo import Collections, get_mongo_db

    mongo = get_mongo_db()
    names = sorted({value for key, value in vars(Collections).items() if key.isupper() and isinstance(value, str)}
                   | {"pipeline_event_log"})
    for name in names:
        await mongo[name].insert_one({"_w31_probe": probe, "actor_id": str(ids.subject), "project_id": None})
    return names


async def _seed_redis(redis: Any, ids: SimpleNamespace, P: SimpleNamespace) -> SimpleNamespace:
    from app.services.ml.training_job import LAST_RUN_KEY

    previous_last = await redis.get(LAST_RUN_KEY)
    keys = SimpleNamespace(membership=f"membership:{ids.subject}", bystander=f"membership:{ids.bystander}",
                           limiter=f"LIMITER/user:{ids.subject}|/api/v1/projects", last=LAST_RUN_KEY)
    await redis.set(keys.membership, json.dumps([str(uuid.uuid4())]), ex=300)
    await redis.set(keys.bystander, json.dumps([str(uuid.uuid4())]), ex=300)
    await redis.set(keys.limiter, "1", ex=300)
    await redis.set(keys.last, json.dumps({"task_id": "t1", "triggered_by": f"user:{P.S.username}"}), ex=600)
    return SimpleNamespace(keys=keys, previous_last=previous_last)


async def _seed_credentials(seeder: Seeder, db: Any, ids: SimpleNamespace, P: SimpleNamespace) -> SimpleNamespace:
    """The subject's access token, refresh token (its record) and API key — each must answer 401 afterwards."""
    import hashlib

    from app.services.auth_session_tokens import issue_access_jwt
    from app.services.refresh_token_service import issue_refresh_token

    access = await issue_access_jwt(db, str(ids.subject))
    refresh = await issue_refresh_token(db, ids.subject)
    raw_key = "tlk_" + _tag(32)
    await seeder.row("api_keys", {"user_id": ids.subject, "name": "ci", "key_hash": hashlib.sha256(raw_key.encode())
                                  .hexdigest(), "key_hint": raw_key[:8], "scopes": [], "is_active": True},
                     text=P.S.full_name)
    bystander_access = await issue_access_jwt(db, str(ids.bystander))
    return SimpleNamespace(access=access, refresh=refresh, api_key=raw_key, bystander_access=bystander_access)


# ── reads for the assertions ──────────────────────────────────────────────────

def _all_tables() -> list[str]:
    from app.models.postgres import Base
    from app.services.subject_data_inventory import NON_ORM_TABLES

    return [table.name for table in Base.metadata.sorted_tables] + sorted(NON_ORM_TABLES)


def _kept_tables() -> set[str]:
    """The scan's exceptions, DERIVED from the inventory: the gate's audit tables and every keep_audit table."""
    from app.services.subject_data_inventory import AUDIT_TABLES, INVENTORY

    return set(AUDIT_TABLES) | {loc.table for loc in INVENTORY if loc.store == "postgres" and loc.erase == "keep_audit"
                                and loc.table}


async def _counts(db: Any) -> dict[str, int]:
    return {name: int((await db.execute(text(f'SELECT count(*) FROM "{name}"'))).scalar() or 0)
            for name in _all_tables()}


async def _sentinel_rows(db: Any, needle: str) -> dict[str, list[str]]:
    """table → the sorted text of every row (``t::text`` = every column) that contains ``needle``."""
    out: dict[str, list[str]] = {}
    for name in _all_tables():
        rows = (await db.execute(text(f'SELECT t::text FROM "{name}" AS t WHERE t::text ILIKE :p'),
                                 {"p": f"%{needle}%"})).scalars().all()
        if rows:
            out[name] = sorted(rows)
    return out


def _bare_app(sessions: Any, *routers: Any, current_user: Any = None) -> Any:
    from fastapi import Depends, FastAPI

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    app = FastAPI()
    for router in routers:
        app.include_router(router)

    @app.get("/probe")
    async def probe(user: Any = Depends(get_current_active_user)) -> dict:
        return {"id": str(user.id)}

    async def _db() -> Any:
        async with sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _db
    if current_user is not None:
        app.dependency_overrides[get_current_active_user] = lambda: current_user
    return app


async def _client(app: Any) -> Any:
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _job(sessions: Any, job_id: uuid.UUID) -> Any:
    from app.models.postgres import SubjectRightsJob

    async with sessions() as db:
        return await db.get(SubjectRightsJob, job_id)


async def _user(sessions: Any, user_id: uuid.UUID) -> Any:
    from app.models.postgres import User

    async with sessions() as db:
        return await db.get(User, user_id)


async def _requeue(sessions: Any, job_id: uuid.UUID) -> None:
    from app.services import subject_rights_job_service as jobs

    async with sessions() as db:
        assert await jobs.requeue(db, job_id, kind="erasure")
        await db.commit()


def _once(original: Any, message: str) -> Any:
    """``original`` that raises on its FIRST call only (the re-entrancy faults of (7))."""
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError(message)
        return await original(*args, **kwargs)

    return flaky


# ── THE proof ─────────────────────────────────────────────────────────────────

async def test_nothing_of_the_person_remains_outside_the_kept_audit_rows(
    sessions: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bson import ObjectId

    from app.db import storage as storage_module
    from app.db.redis_client import get_redis
    from app.routers import subject_erasure as erasure_routes
    from app.services import subject_erasure_service as svc

    monkeypatch.setattr(storage_module.settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    storage = storage_module.LocalStorageProvider(default_bucket="artifacts")
    redis = get_redis()
    now = datetime.now(timezone.utc)
    started = ObjectId.from_datetime(now - timedelta(seconds=5))
    P = _people()
    probe = "w31-" + _tag(10)
    dispatched: list[str] = []
    monkeypatch.setattr(erasure_routes, "_dispatch", lambda job_id: dispatched.append(str(job_id)))

    async with sessions() as db:
        before_counts = await _counts(db)
        seeder = Seeder(db, now=now)
        world = SimpleNamespace(seeder=seeder, ids=None, redis=None, mongo=[])
        try:
            ids = world.ids = await _seed_people(seeder, P)
            seeded = await _seed_postgres(seeder, ids, P)
            objects = await _seed_objects(seeder, storage, ids, P, now)
            credentials = await _seed_credentials(seeder, db, ids, P)
            await db.commit()
            world.mongo = await _seed_mongo(ids, probe)
            world.redis = await _seed_redis(redis, ids, P)
            expected = dict(seeded.expected)
            expected["refresh_token_records.user_id"] += 1     # the issued refresh token's record
            expected["api_keys.user_id"] += 1                   # the minted key
            expected.update({"objects:subject-exports/": 1, "objects:project-exports/": 1,
                             "redis:membership:{id}": 1, "redis:user:{id}": 1, "redis:ml:retrain:last": 1})
            kept_before = {t: rows for t, rows in (await _sentinel_rows(db, P.tag)).items() if t in _kept_tables()}
            await _prove(sessions, storage, redis, P, ids, seeded, objects, credentials, expected, kept_before,
                         started, dispatched, monkeypatch, svc)
        except BaseException:
            await _cleanup(db, world, P, probe, before_counts, strict=False)   # never masks the real failure
            raise
        await _cleanup(db, world, P, probe, before_counts, strict=True)


async def _prove(sessions: Any, storage: Any, redis: Any, P: SimpleNamespace, ids: SimpleNamespace,
                 seeded: SimpleNamespace, objects: SimpleNamespace, credentials: SimpleNamespace,
                 expected: dict[str, int], kept_before: dict[str, list[str]], started: Any, dispatched: list[str],
                 monkeypatch: pytest.MonkeyPatch, svc: Any) -> None:
    from app.routers import auth as auth_routes
    from app.routers import subject_erasure as erasure_routes
    from app.worker.subject_erasure_runner import run_erasure_job

    S = P.S
    probe_app = _bare_app(sessions, auth_routes.router)
    async with await _client(probe_app) as client:   # the credentials work BEFORE (the probe is real)
        assert (await client.get("/probe", headers={"Authorization": f"Bearer {credentials.access}"})).status_code == 200
        assert (await client.get("/probe", headers={"X-API-Key": credentials.api_key})).status_code == 200

    # the ROUTE: the lock-out, one commit, 202
    admin = await _user(sessions, ids.admin)
    async with await _client(_bare_app(sessions, erasure_routes.router, current_user=admin)) as client:
        preview = await client.get(f"/api/v1/users/{ids.subject}/erasure-preview")
        response = await client.post(f"/api/v1/users/{ids.subject}/erasure", json={"confirm_username": S.username})
    assert preview.status_code == 200 and preview.json()["blockers"] == [], preview.text
    assert response.status_code == 202, response.text
    job_id = uuid.UUID(response.json()["id"])
    assert dispatched == [str(job_id)] and response.json()["phase"] == "lockout"
    pseudonym = svc.pseudonym_for(ids.subject)
    assert preview.json()["pseudonym"] == pseudonym
    # the lock-out dropped the membership cache after its commit; a request in flight may refill it before the
    # worker runs — the redis phase is that backstop, so the cache is refilled here and must be counted there
    membership = f"membership:{ids.subject}"
    assert not await redis.exists(membership), "the lock-out drops the membership cache"
    await redis.set(membership, json.dumps([str(uuid.uuid4())]), ex=300)

    # (7a) the Postgres phase raises AFTER the A deletes → the whole transaction rolls back
    monkeypatch.setattr(svc, "_rewrite_values", _once(svc._rewrite_values, "injected after the A deletes"))
    assert await run_erasure_job(job_id, storage=storage, session_factory=sessions) == "incomplete"
    job, row = await _job(sessions, job_id), await _user(sessions, ids.subject)
    assert (job.status, job.phase, job.error_code) == ("running", "lockout", "erasure_incomplete")
    assert row.username == S.username and row.erased_at is None and row.is_active is False
    async with sessions() as db:
        views = (await db.execute(text("SELECT count(*) FROM saved_views WHERE user_id = :u AND NOT is_shared"),
                                  {"u": ids.subject})).scalar()
    assert views == 1, "the A deletes were rolled back with the rest"
    await _requeue(sessions, job_id)

    # (7b) the object phase raises once → running / erasure_incomplete / phase postgres
    monkeypatch.setattr(svc, "remove_project_exports_listing",
                        _once(svc.remove_project_exports_listing, "storage refused"))
    assert await run_erasure_job(job_id, storage=storage, session_factory=sessions) == "incomplete"
    job, row = await _job(sessions, job_id), await _user(sessions, ids.subject)
    assert (job.status, job.phase, job.error_code) == ("running", "postgres", "erasure_incomplete")
    assert row.username == pseudonym and row.erased_at is not None
    await _requeue(sessions, job_id)
    assert await run_erasure_job(job_id, storage=storage, session_factory=sessions) == "succeeded"
    job = await _job(sessions, job_id)
    assert (job.status, job.phase, job.error_code) == ("succeeded", "done", None)

    await _assert_postgres(sessions, P, ids, seeded, kept_before, pseudonym, job)
    await _assert_stores(sessions, storage, redis, P, ids, objects, started, pseudonym)
    async with await _client(probe_app) as client:   # (5) every credential of the subject is refused
        assert (await client.get("/probe", headers={"Authorization": f"Bearer {credentials.access}"})).status_code == 401
        assert (await client.get("/probe", headers={"X-API-Key": credentials.api_key})).status_code == 401
        refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": credentials.refresh})
        assert refreshed.status_code == 401
        bystander = await client.get("/probe", headers={"Authorization": f"Bearer {credentials.bystander_access}"})
        assert bystander.status_code == 200, "the bystander still signs in"
    # (9) every delete / rewrite location's count equals the rows seeded there (absence is not health)
    counts = dict(job.counts or {})
    wrong = {key: (want, counts.get(key)) for key, want in expected.items() if counts.get(key) != want}
    assert not wrong, f"job counts differ from the seeded rows (want, got): {wrong}"


async def _exists(db: Any, table_name: str, row: dict) -> bool:
    from app.models.postgres import Base

    table = Base.metadata.tables[table_name]
    where = [table.c[c.name] == row[c.name] for c in table.primary_key.columns]
    return (await db.execute(select(table.c[table.primary_key.columns[0].name]).where(*where))).first() is not None


async def _assert_postgres(sessions: Any, P: SimpleNamespace, ids: SimpleNamespace, seeded: SimpleNamespace,
                           kept_before: dict[str, list[str]], pseudonym: str, job: Any) -> None:
    from app.services.subject_rights_vocab import ERASED_DISPLAY, ERASED_HASH_MARKER

    S, B = P.S, P.B
    kept = _kept_tables()
    async with sessions() as db:
        # (1) the sentinel scan: every row of every table + auth_token_revocations
        after = await _sentinel_rows(db, P.tag)
        outside = {name: rows[:2] for name, rows in after.items() if name not in kept}
        assert not outside, f"the person remains outside the kept audit rows: {outside}"
        for name in kept:
            assert after.get(name, []) == kept_before.get(name, []), f"{name}: a kept audit row changed"
        assert kept_before, "fail-open: the seeded audit rows must name the subject"
        # the user row (B1): the pseudonymous key every kept row points at
        user = (await db.execute(text("SELECT username, email, full_name, hashed_password, is_active, erased_at, "
                                      "avatar_color, role, mfa_enabled FROM users WHERE id = :u"),
                                 {"u": ids.subject})).one()
        assert (user.username, user.email, user.full_name) == (pseudonym, f"{pseudonym}@erased.invalid", ERASED_DISPLAY)
        assert (user.hashed_password, user.is_active, user.avatar_color, user.role, user.mfa_enabled) == (
            ERASED_HASH_MARKER, False, None, "TESTER", False)
        assert user.erased_at is not None
        # the project's records stay, pointing at the pseudonymised row (and the shared view)
        missing = [name for name, row in seeded.reference_rows if not await _exists(db, name, row)]
        assert not missing and await _exists(db, "saved_views", seeded.shared), missing
        # (6) the bystander: intact, except the mention token in their comment
        bystander = (await db.execute(text("SELECT username, email, full_name, is_active FROM users WHERE id = :u"),
                                      {"u": ids.bystander})).one()
        assert tuple(bystander) == (B.username, B.email, B.full_name, True)
        gone = [name for name, row in seeded.bystander_rows if not await _exists(db, name, row)]
        assert not gone, f"bystander rows deleted: {gone}"
        content = (await db.execute(text("SELECT content FROM test_case_comments WHERE id = :c"),
                                    {"c": seeded.mention["id"]})).scalar_one()
        assert content == seeded.mention["content"].replace(f"@{S.username}", f"@{pseudonym}")
        assert f"@{B.username}" in content
        # (8) the two new access-audit rows carry no sentinel and no pseudonym
        new_rows = (await db.execute(text(
            "SELECT action, t::text AS body FROM access_audit_logs AS t WHERE target_user_id = :u "
            "AND action IN ('subject_erasure_requested', 'subject_erased')"), {"u": ids.subject})).all()
        assert sorted(r.action for r in new_rows) == ["subject_erased", "subject_erasure_requested"]
        for r in new_rows:
            assert P.tag not in r.body.lower() and pseudonym not in r.body, r.action
        erased_row = next(r for r in new_rows if r.action == "subject_erased")
        assert str(job.id) in erased_row.body and P.A.username in erased_row.body


async def _assert_stores(sessions: Any, storage: Any, redis: Any, P: SimpleNamespace, ids: SimpleNamespace,
                         objects: SimpleNamespace, started: Any, pseudonym: str) -> None:
    from app.db.mongo import get_mongo_db
    from app.services.ml.training_job import LAST_RUN_KEY

    tag = P.tag
    # (2) Mongo: every collection, every document written since the test began
    mongo = get_mongo_db()
    for name in await mongo.list_collection_names():
        async for doc in mongo[name].find({"_id": {"$gte": started}}):
            assert tag not in json.dumps(doc, default=str).lower(), f"mongo:{name} names the person"
    # (3) objects: no key carries the sentinel; only the kept retention archive / compliance pack contain it
    listing = await storage.list_objects("", bucket=objects.bucket)
    keys = {str(item.get("key") or item.get("Key")) for item in listing}
    assert objects.keys.subject_export not in keys and objects.keys.listing not in keys
    assert {objects.keys.other, objects.keys.retention, objects.keys.pack} <= keys
    holders = set()
    for key in keys:
        assert tag not in key.lower()
        blob = await storage.get_object_content(key, bucket=objects.bucket)
        if zipfile.is_zipfile(io.BytesIO(blob)):
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                if any(tag in archive.read(member).decode("utf-8", "replace").lower()
                       for member in archive.namelist()):
                    holders.add(key)
    assert holders == {objects.keys.retention, objects.keys.pack}
    async with sessions() as db:
        rows = {r.id: r for r in (await db.execute(text(
            "SELECT id, status, error_code, storage_key FROM project_data_jobs WHERE id = ANY(:i)"),
            {"i": [objects.listing_job, objects.other_job]})).all()}
        export = (await db.execute(text("SELECT status, storage_key FROM subject_rights_jobs WHERE id = :i"),
                                   {"i": objects.export_job})).one()
    assert (rows[objects.listing_job].status, rows[objects.listing_job].error_code) == ("expired", "subject_erased")
    assert rows[objects.listing_job].storage_key is None and rows[objects.other_job].status == "succeeded"
    assert (export.status, export.storage_key) == ("expired", None)
    # (4) Redis: the id-keyed entries are gone, no value names the person, the retrain trail reads the pseudonym
    assert not await redis.exists(f"membership:{ids.subject}")
    assert await redis.exists(f"membership:{ids.bystander}")
    async for key in redis.scan_iter(match="*", count=500):
        name = key.decode() if isinstance(key, bytes) else str(key)
        assert f"user:{ids.subject}" not in name and name != f"membership:{ids.subject}"
        if await redis.type(name) in (b"string", "string"):
            value = await redis.get(name)
            text_value = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")
            assert tag not in text_value.lower(), f"redis {name} names the person"
    last = json.loads(await redis.get(LAST_RUN_KEY))
    assert last["triggered_by"] == f"user:{pseudonym}"


async def _cleanup(db: Any, world: SimpleNamespace, P: SimpleNamespace, probe: str, before: dict[str, int], *,
                   strict: bool) -> None:
    from app.db.mongo import get_mongo_db

    ids = world.ids
    await db.rollback()
    if ids is not None:
        people = [ids.admin, ids.subject, ids.bystander]
        for statement in (
            "DELETE FROM access_audit_logs WHERE target_user_id = :s AND action IN "
            "('subject_erasure_requested', 'subject_erased')",
            "DELETE FROM subject_rights_jobs WHERE subject_user_id = :s AND kind = 'erasure'",
        ):
            await db.execute(text(statement), {"s": ids.subject})
        await db.execute(text("DELETE FROM auth_token_revocations WHERE user_id = ANY(:p)"), {"p": people})
        await db.execute(text("DELETE FROM refresh_token_records WHERE user_id = ANY(:p)"), {"p": people})
        await db.commit()
    failures = await world.seeder.cleanup()
    if world.mongo:
        mongo = get_mongo_db()
        for name in world.mongo:
            await mongo[name].delete_many({"_w31_probe": probe})
    if world.redis is not None:
        from app.db.redis_client import get_redis

        redis, keys = get_redis(), world.redis.keys
        await redis.delete(keys.membership, keys.bystander, keys.limiter)
        if world.redis.previous_last is None:
            await redis.delete(keys.last)
        else:
            await redis.set(keys.last, world.redis.previous_last, ex=600)
    after = await _counts(db)
    moved = {name: (before[name], after[name]) for name in before if before[name] != after.get(name)}
    if strict:
        assert not failures, f"teardown could not delete: {failures}"
        assert not moved, f"tables not back to their pre-test counts: {moved}"
