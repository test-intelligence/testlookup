"""W31 STRAT-12: the erasure service (``services/subject_erasure_service.py``; contract §2.3, §6.3 G-C).

Pure helpers (the pseudonym, the B6 mention token, the value rewrites) plus SQLite
in memory for the reads: the collision ladder, every refusal in E-1's order, and
the preview — which must write NOTHING (a session whose flush raises and whose
connection refuses INSERT / UPDATE / DELETE) and must report a count that times
out as ``measured: False``. The whole Postgres phase is proven on real Postgres
by ``tests/integration/test_subject_erasure_postgres.py``.
"""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.deps import CREDENTIAL_KIND_API_KEY, ApiKeyGrant, _bind_api_key_grant, _bind_credential_kind
from app.models.postgres import Base, SubjectRightsJob, User, UserRole
from app.services import subject_erasure_service as svc
from app.services.subject_data_inventory import INVENTORY, by_key
from app.services.subject_rights_vocab import ERASED_DISPLAY, ERROR_SENTENCES

SERVICE_PY = Path(__file__).resolve().parents[1] / "app" / "services" / "subject_erasure_service.py"


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:  # noqa: D103 - JSONB as SQLite JSON
    return "JSON"


class _AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect: Any, coltype: Any) -> Any:  # noqa: D102
        inner = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            parsed = inner(value) if inner is not None else value
            if isinstance(parsed, datetime) and parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        return process


# ── pure: the pseudonym and the mention token ────────────────────────────────

def test_pseudonym_is_derived_from_the_id_only() -> None:
    uid = uuid.UUID("0123abcd-4567-89ef-0123-456789abcdef")
    assert svc.pseudonym_for(uid) == "erased-0123abcd"
    assert svc.pseudonym_for(uid, 12) == "erased-0123abcd4567"
    assert svc.pseudonym_for(uid, 32) == "erased-" + uid.hex
    assert svc.pseudonym_for(uid) == svc.pseudonym_for(uuid.UUID(str(uid)))
    assert svc.erased_email_for("erased-0123abcd") == "erased-0123abcd@erased.invalid"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("thanks @alice!", "thanks @erased-1!"),
        ("@ALICE please look", "@erased-1 please look"),
        ("(@Alice) and @alice, twice", "(@erased-1) and @erased-1, twice"),
        ("@alice2 is someone else", "@alice2 is someone else"),
        ("@alice_b is someone else", "@alice_b is someone else"),
        ("@alice-b is someone else", "@alice-b is someone else"),
        ("mail x@alice.com stays", "mail x@alice.com stays"),
        ("@@alice is not a token", "@@alice is not a token"),
        ("no mention of alice at all", "no mention of alice at all"),
        ("line one\n@alice\nline three", "line one\n@erased-1\nline three"),
        # A sentence-ending dot ends the token, as the product's mention parser reads it (reference K3s install walk, 2026-11-05).
        ("please confirm @alice.", "please confirm @erased-1."),
        ("ask @alice. Then merge.", "ask @erased-1. Then merge."),
        ("done, @alice...", "done, @erased-1..."),
        ("@alice.smith is someone else", "@alice.smith is someone else"),
        ("@alice.com is not the token", "@alice.com is not the token"),
    ],
)
def test_the_mention_token_is_word_bounded_and_case_insensitive(before: str, after: str) -> None:
    assert svc.rewrite_mentions(before, username="alice", pseudonym="erased-1") == after


@pytest.mark.parametrize("text", [
    "thanks @alice!", "please confirm @alice.", "ask @alice. Then merge.", "done, @alice...", "(@alice)",
    "@alice.smith please", "mail x@alice.com", "@alice2 here", "@alice_b here", "@alice-b here", "@@alice",
    "@ALICE, now", "line\n@alice\nend", "no mention", "email alice@corp.com",
])
def test_the_rewrite_finds_exactly_the_mentions_the_product_resolves(text: str) -> None:
    """The erasure rewrites the ``@<username>`` tokens the product's own mention parser resolved to the person —
    no fewer (a mention left behind is personal data left behind) and no more (another person's name is never
    touched). Both members exist, so the parser's longest match keeps ``@alice.smith`` for alice.smith."""
    from app.services.activity.mentions import find_mentions

    resolved = "alice" in find_mentions(text, ["alice", "alice.smith"])
    assert bool(svc.mention_pattern("alice").search(text)) == resolved, text


def test_a_username_with_regex_characters_is_matched_literally() -> None:
    assert svc.rewrite_mentions("hi @a.b+c", username="a.b+c", pseudonym="erased-2") == "hi @erased-2"
    assert svc.rewrite_mentions("hi @aXb+c", username="a.b+c", pseudonym="erased-2") == "hi @aXb+c"


def _person(**over: Any) -> svc.Identity:
    values = {"user_id": uuid.uuid4(), "username": "zqxwsubj", "email": "zqxw.subj@example.org",
              "full_name": "Zqxw Subjperson"}
    values.update(over)
    return svc.Identity(**values)


def test_value_rewrites_touch_only_the_person() -> None:
    person = _person()
    title = svc._notification_title({"title": "Zqxw Subjperson mentioned you on Login"}, person=person,
                                    pseudonym="erased-9")
    assert title == {"title": "Erased user mentioned you on Login"}
    by_username = svc._notification_title({"title": "zqxwsubj mentioned you on X"}, person=_person(full_name=None),
                                          pseudonym="erased-9")
    assert by_username == {"title": "Erased user mentioned you on X"}
    other = svc._notification_title({"title": "Yvkq Bystand mentioned you"}, person=person, pseudonym="erased-9")
    assert other == {"title": "Yvkq Bystand mentioned you"}
    body = svc._notification_body({"body": "@zqxwsubj see — owner: Zqxw Subjperson"}, person=person,
                                  pseudonym="erased-9")
    assert body == {"body": "@erased-9 see — owner: Erased user"}
    cancelled = svc._cancelled_by_email({"error": "cancelled: by zqxw.subj@example.org"}, email=person.email,
                                        pseudonym="erased-9")
    assert cancelled == {"error": "cancelled: by erased-9"}
    untouched = svc._cancelled_by_email({"error": "cancelled: by other@example.org"}, email=person.email,
                                        pseudonym="erased-9")
    assert untouched == {"error": "cancelled: by other@example.org"}
    payload = svc._payload_requested_by({"event_payload": {"requested_by": "zqxwsubj", "defect_id": "d1"}},
                                        person=person, pseudonym="erased-9")
    assert payload == {"event_payload": {"requested_by": "erased-9", "defect_id": "d1"}}


def test_erased_display_is_the_read_projection() -> None:
    assert svc.erased_display(erased_at=datetime.now(timezone.utc), name="Zqxw Subjperson") == ERASED_DISPLAY
    assert svc.erased_display(erased_at=None, name="Yvkq Bystand") == "Yvkq Bystand"
    assert svc.erased_display(erased_at=None, name=None) is None


# ── SQLite: users + the job tables + every inventory table SQLite can create ──

_PARTIAL = (
    "CREATE UNIQUE INDEX uq_srj_one_live_export ON subject_rights_jobs (subject_user_id) "
    "WHERE kind = 'export' AND status IN ('queued', 'running')",
    "CREATE UNIQUE INDEX uq_srj_one_live_erasure ON subject_rights_jobs (subject_user_id) "
    "WHERE kind = 'erasure' AND status IN ('queued', 'running')",
)


def _sqlite_safe(table: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Postgres-only server defaults (``'{}'::jsonb``, ``gen_random_uuid()``) dropped for the SQLite DDL."""
    for column in table.columns:
        default = column.server_default
        rendered = str(getattr(default, "arg", "")) if default is not None else ""
        if "::" in rendered or "gen_random" in rendered:
            monkeypatch.setattr(column, "server_default", None)


@pytest.fixture
async def maker(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, sqltypes.DateTime: _AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _no_pysqlite_begin(dbapi_connection: Any, _record: Any) -> None:   # SAVEPOINT support
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    names = ["users", "subject_rights_jobs", "project_data_jobs"] + sorted(
        {loc.table for loc in INVENTORY if loc.store == "postgres" and loc.table in Base.metadata.tables}
        - {"users", "subject_rights_jobs", "project_data_jobs"}
    )
    created: set[str] = set()
    for name in names:
        table = Base.metadata.tables[name]
        _sqlite_safe(table, monkeypatch)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all, tables=[table])
            created.add(name)
        except Exception:  # noqa: BLE001 - a Postgres-only CHECK / index: that location reads "not counted"
            continue
    assert {"users", "subject_rights_jobs", "project_data_jobs", "test_case_comments", "api_keys"} <= created
    async with engine.begin() as conn:
        for index in ("uq_srj_one_live_export", "uq_srj_one_live_erasure"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {index}"))
        for ddl in _PARTIAL:
            await conn.execute(text(ddl))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield SimpleNamespace(session=factory, created=created, engine=engine)
    await engine.dispose()


def _user(role: UserRole, name: str, **extra: Any) -> User:
    values: dict[str, Any] = {"id": uuid.uuid4(), "username": name, "email": f"{name}@example.org",
                              "full_name": name.title(), "hashed_password": "x", "role": role.value,
                              "is_active": True}
    values.update(extra)
    return User(**values)


@pytest.fixture
async def world(maker: Any) -> SimpleNamespace:
    people = SimpleNamespace(
        admin=_user(UserRole.ADMIN, "zqxwadmin"),
        subject=_user(UserRole.TESTER, "zqxwsubj"),
        bot=_user(UserRole.TESTER, "zqxwbot", is_service_account=True),
        synthetic=_user(UserRole.QA_LEAD, "zqxwlead", is_synthetic=True),
    )
    async with maker.session() as db:
        db.add_all(vars(people).values())
        await db.commit()
    return people


def _api_key(user: User) -> User:
    _bind_credential_kind(user, CREDENTIAL_KIND_API_KEY)
    return _bind_api_key_grant(user, ApiKeyGrant(key_id=uuid.uuid4(), scopes=(), expires_at=None))


def _job(subject: User, actor: User, *, kind: str, status: str = "queued", error_code: Any = None) -> SubjectRightsJob:
    return SubjectRightsJob(
        id=uuid.uuid4(), kind=kind, status=status, source="admin", subject_user_id=subject.id,
        requested_by_id=actor.id, counts={}, attempts=0, requested_at=datetime.now(timezone.utc),
        error_code=error_code,
    )


async def test_the_collision_ladder_skips_a_live_username_or_email(maker: Any, world: SimpleNamespace) -> None:
    subject = world.subject
    async with maker.session() as db:
        assert await svc.choose_pseudonym(db, subject) == svc.pseudonym_for(subject.id, 8)
        db.add(_user(UserRole.TESTER, "squat8", username=svc.pseudonym_for(subject.id, 8)))
        db.add(_user(UserRole.TESTER, "squat12", email=svc.erased_email_for(svc.pseudonym_for(subject.id, 12))))
        await db.commit()
        assert await svc.choose_pseudonym(db, subject) == svc.pseudonym_for(subject.id, 16)
        # a re-run finds its own row already holding its pseudonym and reuses it (deterministic)
        row = await db.get(User, subject.id)
        row.username = svc.pseudonym_for(subject.id, 16)
        assert await svc.choose_pseudonym(db, row) == svc.pseudonym_for(subject.id, 16)
        await db.rollback()


# ── E-1: every refusal, in order ─────────────────────────────────────────────

async def test_every_refusal_in_e1_order(maker: Any, world: SimpleNamespace) -> None:
    admin, subject = world.admin, world.subject
    async with maker.session() as db:
        # session_required comes before everything — even an unknown target
        assert await svc.refusal(db, subject=None, actor=_api_key(_user(UserRole.ADMIN, "zqxwkey"))) == "session_required"
        assert await svc.refusal(db, subject=subject, actor=_api_key(_user(UserRole.ADMIN, "zqxwkey2"))) \
            == "session_required"
        assert await svc.refusal(db, subject=None, actor=admin) == "user_not_found"
        assert await svc.refusal(db, subject=world.bot, actor=admin) == "not_a_person"
        assert await svc.refusal(db, subject=world.synthetic, actor=admin) == "not_a_person"
        # the admin is also the last active admin: self_erasure is answered first
        assert await svc.blockers(db, subject=admin, actor=admin) == ["self_erasure", "last_admin"]
        assert await svc.refusal(db, subject=subject, actor=admin) is None

        # an export running blocks; an erasure running (no error) is erasure_running — before the export
        db.add(_job(subject, admin, kind="export", status="running"))
        await db.commit()
        assert await svc.refusal(db, subject=subject, actor=admin) == "export_blocks_erasure"
        erasure = _job(subject, admin, kind="erasure", status="running")
        db.add(erasure)
        await db.commit()
        assert await svc.blockers(db, subject=subject, actor=admin) == ["erasure_running", "export_blocks_erasure"]
        # an incomplete erasure is RESUMABLE (no erasure_running), also once the row reads erased
        erasure.error_code = "erasure_incomplete"
        subject_row = await db.get(User, subject.id)
        subject_row.erased_at = datetime.now(timezone.utc)
        await db.commit()
        assert await svc.blockers(db, subject=subject_row, actor=admin) == ["export_blocks_erasure"]
        # erased and no unfinished erasure → user_erased
        erasure.status = "succeeded"
        await db.execute(text("UPDATE subject_rights_jobs SET status = 'succeeded'"))
        await db.commit()
        assert await svc.refusal(db, subject=subject_row, actor=admin) == "user_erased"

        # last_admin: the target is the only ACTIVE admin (the caller's own row is inactive)
        other = _user(UserRole.ADMIN, "zqxwother")
        db.add(other)
        await db.commit()
        caller = await db.get(User, admin.id)
        caller.is_active = False
        await db.commit()
        assert await svc.refusal(db, subject=other, actor=caller) == "last_admin"
        caller.is_active = True
        await db.commit()
        assert await svc.refusal(db, subject=other, actor=caller) is None


# ── W38: no human actor (a SCIM DELETE in erase mode) ────────────────────────

async def test_no_actor_skips_only_the_session_and_self_checks(maker: Any, world: SimpleNamespace) -> None:
    admin, subject = world.admin, world.subject
    async with maker.session() as db:
        assert await svc.refusal(db, subject=None, actor=None) == "user_not_found"
        assert await svc.refusal(db, subject=subject, actor=None) is None
        assert await svc.refusal(db, subject=world.bot, actor=None) == "not_a_person"
        # the only active admin: no self_erasure without an actor, last_admin still applies
        assert await svc.blockers(db, subject=admin, actor=None) == ["last_admin"]
        db.add(_job(subject, admin, kind="export", status="running"))
        await db.commit()
        assert await svc.refusal(db, subject=subject, actor=None) == "export_blocks_erasure"
        erasure = _job(subject, admin, kind="erasure", status="queued")
        db.add(erasure)
        await db.commit()
        assert await svc.blockers(db, subject=subject, actor=None) == ["erasure_running", "export_blocks_erasure"]


async def _lock_out(maker: Any, subject: User, actor: Any, **extra: Any) -> tuple[SubjectRightsJob, list[Any]]:
    from app.models.postgres import AccessAuditLog

    async with maker.session() as db:
        job = _job(subject, subject, kind="erasure")
        job.requested_by_id = actor.id if actor is not None else None
        job.source = "admin" if actor is not None else "scim"
        db.add(job)
        await db.flush()
        await svc.stage_lockout(db, subject=subject, actor=actor, job=job, **extra)
        await db.commit()
        audit = list((await db.execute(select(AccessAuditLog))).scalars().all())
    return job, audit


async def test_the_lockout_audits_a_null_actor_and_the_callers_extra(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _revoke(user_id: uuid.UUID, db: Any = None) -> None:
        return None

    monkeypatch.setattr("app.core.token_revocation.revoke_all_user_tokens", _revoke)
    token_id = str(uuid.uuid4())
    job, audit = await _lock_out(maker, world.subject, None,
                                 audit_extra={"via": "scim", "scim_token_id": token_id})
    assert len(audit) == 1 and audit[0].action == "subject_erasure_requested"
    assert (audit[0].actor_user_id, audit[0].actor_name) == (None, None)
    assert audit[0].target_user_id == world.subject.id and audit[0].before_value == {"is_active": True}
    assert audit[0].after_value == {"job_id": str(job.id), "via": "scim", "scim_token_id": token_id}
    assert (job.phase, job.status) == ("lockout", "queued")
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).is_active is False


async def test_the_lockout_extra_never_replaces_the_job_id(
    maker: Any, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _revoke(user_id: uuid.UUID, db: Any = None) -> None:
        raise AssertionError("refused before any write")

    monkeypatch.setattr("app.core.token_revocation.revoke_all_user_tokens", _revoke)
    with pytest.raises(ValueError):
        await _lock_out(maker, world.subject, world.admin, audit_extra={"job_id": "forged"})
    async with maker.session() as db:
        assert (await db.get(User, world.subject.id)).is_active is True


def test_confirm_is_exact_and_case_sensitive() -> None:
    subject = _user(UserRole.TESTER, "zqxwSubj")
    assert svc.confirm_matches(subject, "zqxwSubj")
    assert not svc.confirm_matches(subject, "zqxwsubj")
    assert not svc.confirm_matches(subject, " zqxwSubj")
    assert not svc.confirm_matches(subject, None)
    assert svc.REFUSAL_STATUS["confirm_mismatch"] == 422
    assert svc.refusal_detail("confirm_mismatch") == {"code": "confirm_mismatch",
                                                      "message": ERROR_SENTENCES["confirm_mismatch"]}
    assert set(svc.REFUSAL_STATUS) <= set(ERROR_SENTENCES)


# ── the preview: a GET of counts that writes NOTHING ─────────────────────────

async def _seed_preview_rows(maker: Any, world: SimpleNamespace) -> None:
    subject, admin = world.subject, world.admin
    case_id = uuid.uuid4()
    rows = [
        ("test_case_comments", {"id": uuid.uuid4(), "test_case_id": case_id, "author_id": admin.id,
                                "content": "ping @zqxwsubj and @ZQXWSUBJ"}),
        ("test_case_comments", {"id": uuid.uuid4(), "test_case_id": case_id, "author_id": admin.id,
                                "content": "@zqxwsubj2 is someone else"}),
        ("test_case_comments", {"id": uuid.uuid4(), "test_case_id": case_id, "author_id": subject.id,
                                "content": "my own words"}),
        ("saved_views", {"id": uuid.uuid4(), "user_id": subject.id, "name": "mine", "filters": {}, "is_shared": False}),
        ("saved_views", {"id": uuid.uuid4(), "user_id": subject.id, "name": "team", "filters": {}, "is_shared": True}),
        ("user_ui_dismissals", {"id": uuid.uuid4(), "user_id": subject.id, "dismissal_key": "hint-a"}),
        ("api_keys", {"id": uuid.uuid4(), "user_id": subject.id, "name": "ci", "key_hash": "h" * 20,
                      "key_hint": "tl_zz", "scopes": []}),
    ]
    async with maker.session() as db:
        for table, values in rows:
            await db.execute(Base.metadata.tables[table].insert().values(**values))
        await db.commit()


class _WriteGuard:
    """Every INSERT / UPDATE / DELETE on the engine raises, and so does a session flush."""

    def __init__(self, maker: Any, db: Any) -> None:
        self.engine, self.session, self.seen = maker.engine.sync_engine, db.sync_session, []

    def _statement(self, conn: Any, cursor: Any, statement: str, *_a: Any) -> None:
        self.seen.append(statement.split(None, 1)[0].upper())
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            raise AssertionError(f"the preview wrote: {statement[:80]}")

    def _flush(self, *_a: Any) -> None:
        raise AssertionError("the preview flushed")

    def __enter__(self) -> "_WriteGuard":
        event.listen(self.engine, "before_cursor_execute", self._statement)
        event.listen(self.session, "before_flush", self._flush)
        return self

    def __exit__(self, *_exc: Any) -> None:
        event.remove(self.engine, "before_cursor_execute", self._statement)
        event.remove(self.session, "before_flush", self._flush)


async def test_the_preview_writes_nothing_and_counts_each_location(maker: Any, world: SimpleNamespace) -> None:
    await _seed_preview_rows(maker, world)
    async with maker.session() as db:
        subject = await db.get(User, world.subject.id)
        admin = await db.get(User, world.admin.id)
        with _WriteGuard(maker, db) as guard:
            result = await svc.preview(db, subject=subject, actor=admin)
    assert guard.seen and not {"INSERT", "UPDATE", "DELETE"} & set(guard.seen)
    changes = {entry.key: entry for entry in result.changes}
    assert changes["users.*"].count == 1
    assert changes["saved_views.user_id"].count == 1           # the shared view is the project's: kept
    assert changes["user_ui_dismissals.user_id"].count == 1
    assert changes["api_keys.user_id"].count == 1
    assert changes["test_case_comments.content"].count == 1    # the real token only; @zqxwsubj2 is not it
    assert all(entry.action in ("delete", "pseudonymise") for entry in result.changes)
    assert result.pseudonym == svc.pseudonym_for(subject.id)
    assert result.blockers == [] and result.erasure_job is None
    assert result.display == "Zqxwsubj" and result.username == "zqxwsubj" and result.role == "TESTER"
    # every location left out was counted 0 or is not counted ("not counted" reads measured False)
    for entry in [*result.changes, *result.kept, *result.open_items]:
        assert entry.measured == (entry.count is not None)
    # a location whose table SQLite cannot build reads "not counted", never 0
    missing = [loc.key for loc in INVENTORY if loc.store == "postgres" and loc.erase in ("delete", "pseudonymise")
               and loc.table not in maker.created and loc.table != "users"]
    for key in missing:
        assert changes[key].measured is False and changes[key].count is None


async def test_a_timed_out_count_reads_not_measured(maker: Any, world: SimpleNamespace) -> None:
    """A count statement cancelled by the statement timeout is ``count: None, measured: False``; the
    other counts still run (the cancelled one rolled back to its SAVEPOINT)."""
    await _seed_preview_rows(maker, world)

    class QueryCanceled(Exception):
        """canceling statement due to statement timeout"""

    def _cancel(conn: Any, cursor: Any, statement: str, *_a: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT COUNT") and "user_ui_dismissals" in statement:
            raise QueryCanceled("canceling statement due to statement timeout")

    event.listen(maker.engine.sync_engine, "before_cursor_execute", _cancel)
    try:
        async with maker.session() as db:
            subject = await db.get(User, world.subject.id)
            admin = await db.get(User, world.admin.id)
            result = await svc.preview(db, subject=subject, actor=admin)
    finally:
        event.remove(maker.engine.sync_engine, "before_cursor_execute", _cancel)
    changes = {entry.key: entry for entry in result.changes}
    assert changes["user_ui_dismissals.user_id"].count is None
    assert changes["user_ui_dismissals.user_id"].measured is False
    assert changes["saved_views.user_id"].count == 1 and changes["api_keys.user_id"].count == 1


async def test_the_preview_lists_blockers_and_the_unfinished_job(maker: Any, world: SimpleNamespace) -> None:
    async with maker.session() as db:
        db.add(_job(world.subject, world.admin, kind="erasure", status="running", error_code="erasure_incomplete"))
        db.add(_job(world.subject, world.admin, kind="export", status="queued"))
        await db.commit()
        result = await svc.preview(db, subject=await db.get(User, world.subject.id),
                                   actor=await db.get(User, world.admin.id))
    assert [blocker.code for blocker in result.blockers] == ["export_blocks_erasure"]
    assert result.blockers[0].message == ERROR_SENTENCES["export_blocks_erasure"]
    assert result.erasure_job is not None and result.erasure_job.kind == "erasure"
    assert result.erasure_job.error is not None and result.erasure_job.error.code == "erasure_incomplete"


def test_every_delete_and_rewrite_location_has_a_row_filter() -> None:
    person = _person()
    for location in INVENTORY:
        if location.store != "postgres" or location.erase not in ("delete", "pseudonymise"):
            continue
        clause = svc.match_clause(location, person)
        if location.key == "users.*":
            assert clause is None
            continue
        assert clause is not None, location.key
        compiled = str(clause.compile(dialect=postgresql.dialect()))
        assert location.column in compiled or location.key == "saved_views.user_id", location.key
    # the A-list never names an audit table, and the open review filter keeps only live reviews
    review = svc.match_clause(by_key("test_case_reviews.reviewer_id"), person)
    assert "status" in str(review.compile(dialect=postgresql.dialect()))


def test_the_module_never_commits_and_never_writes_an_audit_model() -> None:
    tree = ast.parse(SERVICE_PY.read_text(encoding="utf-8"))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not {"commit", "rollback"} & calls
    source = SERVICE_PY.read_text(encoding="utf-8")
    for model in ("AccessAuditLog", "IdentityEvent", "SettingsAuditLog", "TestCaseAuditLog", "ProjectActivityEvent"):
        assert model not in source
    # no log line carries a person: every logger call passes kwargs that are ids / counts / types only
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                isinstance(node.func.value, ast.Name) and node.func.value.id == "_slog":
            assert len(node.args) == 1
            for keyword in node.keywords:
                assert keyword.arg in {"job_id", "user_id", "error_type", "sessions", "locations", "rows"}, \
                    keyword.arg
