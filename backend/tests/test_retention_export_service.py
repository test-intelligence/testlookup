"""W22b ACT-03 — the export service: estimates, the choice, the export phase.

The rule every test here leans on: NOTHING is deleted on an unproven archive.
``run_export`` raises ``ExportFailed`` on any build / write / re-read failure
and never touches a run; the doubles below record every statement so a test
can prove no DELETE was issued. Also pinned: ONE archive builder (E-1) and
that neither new module imports ``app.services.activity`` (the criteria
preview stays honestly "unrecorded" in the activity-coverage baseline).
"""
from __future__ import annotations

import asyncio
import inspect
import io
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import compliance_pack_service as packs
from app.services import retention_export_content as content
from app.services import retention_export_service as svc
from app.services.retention_vocab import EXPORT_ERROR_SENTENCES, EXPORT_JOB_ERRORS

PROJECT = uuid.UUID("00000000-0000-4000-8000-0000000000aa")


# ── doubles ──────────────────────────────────────────────────────────────────


class MemStorage:
    """An object store in a dict; ``(bucket, key) -> bytes``."""

    def __init__(self, objects=None):
        self.objects: dict[tuple, bytes] = dict(objects or {})
        self.deleted: list[str] = []
        self.reads = 0

    async def put_object(self, key, content, content_type="application/json", bucket=None):
        self.objects[(bucket, key)] = bytes(content)

    async def put_object_stream(self, key, fileobj, *, size, content_type="application/zip", bucket=None):
        fileobj.seek(0)
        self.objects[(bucket, key)] = fileobj.read()

    async def stream_object(self, key, bucket=None):
        self.reads += 1
        data = self.objects[(bucket, key)]
        for i in range(0, len(data), 4096):
            yield data[i:i + 4096]

    async def get_object_content(self, key, bucket=None):
        return self.objects[(bucket, key)]

    async def delete_object(self, key, bucket=None):
        self.deleted.append(key)
        self.objects.pop((bucket, key), None)

    async def list_objects(self, prefix, bucket=None):
        return [
            {"Key": k, "Size": len(v)} for (b, k), v in sorted(self.objects.items(), key=lambda i: i[0][1])
            if b is None and k.startswith(prefix)
        ]


class SizedStorage(MemStorage):
    """Lists sizes it does not hold (a 26 MiB object without 26 MiB in memory)."""

    def __init__(self, objects=None, sizes=None):
        super().__init__(objects)
        self.sizes = dict(sizes or {})

    async def list_objects(self, prefix, bucket=None):
        listed = {o["Key"]: o["Size"] for o in await super().list_objects(prefix, bucket)}
        listed.update({k: s for k, s in self.sizes.items() if k.startswith(prefix)})
        return [{"Key": k, "Size": s} for k, s in sorted(listed.items())]


class RaisingListStorage(MemStorage):
    async def list_objects(self, prefix, bucket=None):
        raise ConnectionError("object store down")


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class FakeDB:
    """Routes a statement by its SQL text (first matching fragment wins) and
    records every statement, so a test can prove no DELETE was ever issued."""

    def __init__(self, rules=()):
        self.rules = list(rules)
        self.sql: list[str] = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, *_a, **_kw):
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.sql.append(sql)
        for fragment, answer in self.rules:
            if fragment in sql:
                return _Rows(answer(compiled.params) if callable(answer) else answer)
        return _Rows()


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()


class MemCollection:
    def __init__(self, docs=()):
        self.docs = list(docs)

    @staticmethod
    def _match(doc, query):
        for field, cond in query.items():
            value = doc.get(field)
            if isinstance(cond, dict) and "$in" in cond:
                if value not in cond["$in"]:
                    return False
            elif value != cond:
                return False
        return True

    def find(self, query, sort=None):
        docs = [d for d in self.docs if self._match(d, query)]
        return _Cursor(sorted(docs, key=lambda d: str(d.get("_id"))))

    async def find_one(self, query):
        found = [d for d in self.docs if self._match(d, query)]
        return found[0] if found else None

    async def count_documents(self, query):
        return len([d for d in self.docs if self._match(d, query)])


class MemMongo:
    def __init__(self, collections=None, avg=100):
        self.collections = {name: MemCollection(docs) for name, docs in (collections or {}).items()}
        self.avg = avg

    def __getitem__(self, name):
        return self.collections.setdefault(name, MemCollection())

    async def command(self, name, collection):
        return {"avgObjSize": self.avg}


# ── E-1: one builder; no activity import ─────────────────────────────────────


def test_there_is_one_archive_builder_and_both_paths_reach_it():
    """One ZIP writer, one manifest builder; the release path and the
    retention export both CALL ``build_archive`` (the call, not the name)."""
    source = inspect.getsource(packs)

    def writers(text):
        return [line for line in text.splitlines() if "zipfile.ZipFile(" in line and '"w"' in line]

    assert len(writers(source)) == 1, writers(source)
    assert writers(inspect.getsource(packs.build_archive)) == writers(source)
    assert source.count("def _build_manifest") == 1
    assert "build_archive(" in inspect.getsource(packs._build_pack_payload)
    assert "_build_pack_payload(" in inspect.getsource(packs.generate_pack)
    assert "build_archive(" in inspect.getsource(svc.run_export)
    assert "_sign_pack(" in inspect.getsource(svc.run_export)
    assert "zipfile.ZipFile(" not in inspect.getsource(svc) + inspect.getsource(content)


@pytest.mark.parametrize("module", [svc, content])
def test_neither_export_module_imports_the_activity_ledger(module):
    """The preview calls the estimate; an activity import here would make the
    baselined-unrecorded preview look recorded (``backend.activity-coverage``)."""
    source = inspect.getsource(module)
    assert "app.services.activity" not in source
    assert "record_activity" not in source


# ── E-2 / E-9: the per-run byte map (safe prefixes only) ────────────────────


def _listing(*items):
    return sorted(({"Key": k, "Size": s} for k, s in items), key=lambda o: o["Key"])


def test_bytes_under_run_counts_the_safe_prefixes_once_each():
    run = uuid.uuid4()
    other = uuid.uuid4()
    listing = _listing(
        (f"uploads/{PROJECT}/{run}/a.json", 10),
        (f"uploads/{PROJECT}/{run}/sub/b.png", 20),
        (f"uploads/{PROJECT}/{other}/c.json", 400),
        (f"{PROJECT}/custom/{run}/d.log", 7),
        ("uploads/shared/e.json", 999),
    )
    # The custom prefix is inside the project: counted. A shared prefix is refused (RET-D9).
    assert svc.bytes_under_run(listing, project_id=PROJECT, run_id=run,
                               minio_prefix=f"{PROJECT}/custom/{run}/") == 37
    assert svc.bytes_under_run(listing, project_id=PROJECT, run_id=run,
                               minio_prefix="uploads/shared/") == 30
    # A sub-prefix of the canonical one is not counted twice.
    assert svc.bytes_under_run(listing, project_id=PROJECT, run_id=run,
                               minio_prefix=f"uploads/{PROJECT}/{run}/sub/") == 30
    assert svc.bytes_under_run(listing, project_id=PROJECT, run_id=run,
                               minio_prefix=f"{PROJECT}/custom/{run}/", text_only=True) == 17


@pytest.mark.asyncio
async def test_list_project_objects_lists_the_two_roots_once_each():
    calls = []

    class _S(MemStorage):
        async def list_objects(self, prefix, bucket=None):
            calls.append(prefix)
            return await super().list_objects(prefix, bucket)

    storage = _S({(None, f"uploads/{PROJECT}/r/x.json"): b"12", (None, f"{PROJECT}/y.log"): b"1"})
    listing = await svc.list_project_objects(storage, project_id=PROJECT)
    assert calls == [f"uploads/{PROJECT}/", f"{PROJECT}/"]
    assert [o["Key"] for o in listing] == sorted(o["Key"] for o in listing)
    assert await svc.per_run_object_bytes(RaisingListStorage(), project_id=PROJECT, prefixes={}) is None


# ── E-2: the estimate ────────────────────────────────────────────────────────


def _estimate_db(run_ids, cases=10, failed=3):
    rows = [(r, cases, failed) for r in run_ids]
    return FakeDB(rules=[("FROM test_cases", rows)])


@pytest.mark.asyncio
async def test_the_estimate_applies_the_tier_formulas_over_measured_parts():
    runs = [uuid.uuid4(), uuid.uuid4()]
    storage = MemStorage({
        (None, f"uploads/{PROJECT}/{runs[0]}/a.json"): b"x" * 1000,
        (None, f"uploads/{PROJECT}/{runs[1]}/b.png"): b"y" * 5000,
    })
    mongo = MemMongo({"raw_allure_json": [{"_id": 1, "test_run_id": str(runs[0])},
                                          {"_id": 2, "test_run_id": str(runs[1])}],
                      "run_summaries": [{"_id": 3, "test_run_id": str(runs[0])}],
                      # W25d HYG-02: a retired collection is neither reclaimed nor archived.
                      "execution_logs": [{"_id": 4, "test_run_id": str(runs[1])}]}, avg=100)
    db = _estimate_db(runs)

    est = await svc.estimate_export(db, project_id=PROJECT, run_ids=runs, mongo=mongo,
                                    storage=storage, max_bytes=10_000_000, collect_ttl_hours=72)

    summary = 2048 * 2 + 256 * 20
    evidence = summary + 4096 * 6 + 32768 * 2
    full = evidence + 200 + 1000          # raw allure (2 docs x 100) + the one text object
    assert [t["tier"] for t in est["tiers"]] == ["summary", "evidence", "full"]
    assert [t["archive_bytes"] for t in est["tiers"]] == [summary, evidence, full]
    assert all(t["measured"] and not t["over_cap"] for t in est["tiers"])
    assert est["reclaim_object_bytes"] == 6000 and est["reclaim_mongo_bytes"] == 300
    assert est["reclaim_bytes"] == 6300 and est["reclaim_measured"] is True
    assert est["exportable"] is True and est["refusal"] is None
    assert est["note"] == "Archive, not a backup — it cannot be re-imported."
    assert "ANY" in next(s for s in db.sql if "test_cases" in s), "one uuid[] statement"
    assert len([s for s in db.sql if "test_cases" in s]) == 1


@pytest.mark.asyncio
async def test_a_storage_outage_is_not_measured_never_zero_and_summary_survives():
    runs = [uuid.uuid4()]
    est = await svc.estimate_export(_estimate_db(runs), project_id=PROJECT, run_ids=runs,
                                    mongo=MemMongo(), storage=RaisingListStorage(), max_bytes=10**9)
    tiers = {t["tier"]: t for t in est["tiers"]}
    assert est["reclaim_measured"] is False
    assert est["reclaim_bytes"] is None and est["reclaim_object_bytes"] is None
    assert tiers["summary"]["measured"] is True and tiers["summary"]["archive_bytes"] > 0
    assert tiers["full"]["measured"] is False and tiers["full"]["archive_bytes"] is None
    assert tiers["full"]["over_cap"] is False, "the build enforces the cap, not a guess"


@pytest.mark.asyncio
async def test_over_the_cap_and_over_the_run_limit():
    runs = [uuid.uuid4()]
    est = await svc.estimate_export(_estimate_db(runs, cases=1000), project_id=PROJECT, run_ids=runs,
                                    mongo=MemMongo(), storage=MemStorage(), max_bytes=100_000)
    assert [t["over_cap"] for t in est["tiers"]] == [True, True, True]
    many = [uuid.uuid4() for _ in range(2001)]
    est = await svc.estimate_export(FakeDB(), project_id=PROJECT, run_ids=many,
                                    mongo=MemMongo(), storage=MemStorage(), max_bytes=10**9)
    assert est["exportable"] is False and est["refusal"] == EXPORT_ERROR_SENTENCES["too_many_runs"]
    est = await svc.estimate_export(FakeDB(), project_id=PROJECT, run_ids=[],
                                    mongo=MemMongo(), storage=MemStorage(), max_bytes=10**9)
    assert est["exportable"] is False and est["refusal"] == EXPORT_ERROR_SENTENCES["nothing_to_export"]


# ── E-8: the execute-time choice ─────────────────────────────────────────────


@pytest.mark.parametrize("choice, run_count, estimate, code, status", [
    ({"tier": "everything", "target": "collect"}, 3, None, "unknown_tier", 422),
    ({"tier": "summary", "target": "external"}, 3, None, "unknown_target", 422),
    ({"tier": "summary", "target": "collect"}, 0, None, "nothing_to_export", 409),
    ({"tier": "summary", "target": "collect"}, 2001, None, "too_many_runs", 409),
    ({"tier": "full", "target": "retain"}, 3,
     {"tiers": [{"tier": "full", "archive_bytes": 9, "measured": True, "over_cap": True}]}, "over_cap", 409),
])
def test_each_refusal_carries_its_sentence(choice, run_count, estimate, code, status):
    with pytest.raises(svc.ExportRefused) as caught:
        svc.validate_choice(choice, estimate=estimate, run_count=run_count)
    assert (caught.value.code, caught.value.status_code) == (code, status)
    assert caught.value.detail() == {"code": code, "message": EXPORT_ERROR_SENTENCES[code]}


def test_an_unmeasured_tier_is_not_refused_and_objects_are_accepted():
    estimate = {"tiers": [{"tier": "full", "archive_bytes": None, "measured": False, "over_cap": False}]}
    svc.validate_choice(SimpleNamespace(tier="full", target="collect"), estimate=estimate, run_count=5)
    svc.validate_choice({"tier": "evidence", "target": "retain"}, estimate=None, run_count=2000)


# ── E-5: the export phase ────────────────────────────────────────────────────


def _hold(reason="Audit sentinel"):
    from app.services.retention_hold_service import HoldRef

    return HoldRef(id=uuid.uuid4(), entity_type="run", entity_id=uuid.uuid4(),
                   entity_label="run r3", reason=reason, expires_at=None)


@pytest.fixture
def export_env(monkeypatch):
    """run_export with its collaborators stubbed: holds (none), the existing
    runs (every requested one), unsigned signing, small fixed members."""
    from app.services import compliance_signing, retention_hold_service

    env = SimpleNamespace(holds={}, existing=None, members=None, hold_error=None)

    async def _holds(db, *, project_id, run_ids, now):
        if env.hold_error:
            raise env.hold_error
        return dict(env.holds)

    async def _members(db, scope, *, mongo, storage, deletion, stats=None):
        env.deletion = deletion
        env.scope = scope
        yield "README.md", b"readme"
        yield "deletion.json", content._dump(deletion)
        for run_id in scope.run_ids:
            yield f"test_cases/{run_id}.json", env.members or b"[]"
        if stats is not None:
            stats["objects_not_archived"] = 2

    monkeypatch.setattr(retention_hold_service, "holds_covering_runs", _holds)
    monkeypatch.setattr(content, "run_members", _members)
    monkeypatch.setattr(compliance_signing, "active_signing_key", AsyncMock(return_value=None))
    monkeypatch.setattr(compliance_signing, "public_keys", AsyncMock(return_value={}))
    return env


def _existing_db(ids):
    return FakeDB(rules=[("FROM test_runs", [(r,) for r in ids])])


async def _export(db, ids, storage, **kw):
    kw.setdefault("choice", {"tier": "evidence", "target": "collect"})
    return await svc.run_export(
        db, job_id=kw.pop("job_id", uuid.uuid4()), project_id=PROJECT, run_ids=ids,
        mongo=MemMongo(), storage=storage, now=datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc),
        deletion={"project_name": "Payments sentinel"}, **kw,
    )


def _no_deletes(db):
    return not [s for s in db.sql if s.lstrip().upper().startswith("DELETE")]


@pytest.mark.asyncio
async def test_a_verified_export_is_re_read_from_storage(export_env):
    ids = [uuid.uuid4(), uuid.uuid4()]
    storage, db, written = MemStorage(), _existing_db(ids), []

    async def _on_written(key):
        written.append((key, storage.reads))

    outcome = await _export(db, ids, storage, on_written=_on_written)

    stored = storage.objects[(packs.COMPLIANCE_BUCKET, outcome.location)]
    assert outcome.location.startswith(f"exports/2026/10/28/{PROJECT}/")
    assert written == [(outcome.location, 0)], "the location is recorded BEFORE the re-read"
    assert storage.reads == 1 and outcome.verify_attempts == 1
    assert outcome.archive_sha256 == packs.sha256_of_file(io.BytesIO(stored))
    assert outcome.bytes == len(stored)
    with zipfile.ZipFile(io.BytesIO(stored)) as zf:
        names = zf.namelist()
        assert names[-1] == "manifest.json", "the manifest is the LAST member of a run archive"
        assert packs._sha256(zf.read("manifest.json")) == outcome.manifest_sha256
    assert outcome.signing == {"state": "unsigned", "reason": "key_unreadable"}
    assert outcome.objects_not_archived == 2 and outcome.run_ids_archived == ids
    assert _no_deletes(db)


@pytest.mark.asyncio
async def test_a_failed_write_deletes_nothing(export_env):
    class _Fails(MemStorage):
        async def put_object_stream(self, *a, **kw):
            raise ConnectionError("store down")

    ids = [uuid.uuid4()]
    db = _existing_db(ids)
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(db, ids, _Fails())
    assert caught.value.code == "write"
    assert caught.value.message == EXPORT_JOB_ERRORS["write"].format(error_type="ConnectionError")
    assert _no_deletes(db)


@pytest.mark.asyncio
async def test_flipped_bytes_on_the_re_read_fail_and_remove_the_object(export_env):
    class _Flips(MemStorage):
        async def stream_object(self, key, bucket=None):
            data = bytearray(self.objects[(bucket, key)])
            data[len(data) // 2] ^= 0xFF
            yield bytes(data)

    ids = [uuid.uuid4()]
    storage, db = _Flips(), _existing_db(ids)
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(db, ids, storage)
    assert caught.value.code == "unverified" and caught.value.params == {"reason": "digest mismatch"}
    assert not storage.objects and len(storage.deleted) == 1, "the unproven object is removed"
    assert _no_deletes(db)


@pytest.mark.asyncio
async def test_a_slow_store_is_retried_and_never_counted_verified_early(export_env):
    class _Slow(MemStorage):
        def __init__(self, failures):
            super().__init__()
            self.failures = failures

        async def stream_object(self, key, bucket=None):
            if self.failures:
                self.failures -= 1
                raise asyncio.TimeoutError()
            async for chunk in super().stream_object(key, bucket):
                yield chunk

    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    ids = [uuid.uuid4()]
    outcome = await _export(_existing_db(ids), ids, _Slow(2), sleep=_sleep)
    assert outcome.verify_attempts == 3 and slept == [30, 60]

    storage = _Slow(3)
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(_existing_db(ids), ids, storage, sleep=_sleep)
    assert caught.value.params == {"reason": "storage did not answer"}
    assert not storage.objects


@pytest.mark.asyncio
async def test_an_archive_over_the_cap_fails_over_cap(export_env):
    export_env.members = bytes(range(256)) * 64          # incompressible enough to pass 1 KiB
    ids = [uuid.uuid4()]
    storage = MemStorage()
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(_existing_db(ids), ids, storage, max_bytes=1024)
    assert caught.value.code == "over_cap"
    assert not storage.objects, "nothing was written"


@pytest.mark.asyncio
async def test_an_unreadable_hold_set_fails_closed(export_env):
    export_env.hold_error = RuntimeError("statement failed")
    ids = [uuid.uuid4()]
    storage = MemStorage()
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(_existing_db(ids), ids, storage)
    assert caught.value.code == "holds_unreadable"
    assert caught.value.message == EXPORT_JOB_ERRORS["holds_unreadable"]
    assert not storage.objects


@pytest.mark.asyncio
async def test_a_held_run_is_excluded_and_a_missing_run_is_named(export_env):
    held, kept, gone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ref = _hold()
    export_env.holds = {held: [ref]}
    outcome = await _export(_existing_db([held, kept]), [held, kept, gone], MemStorage())

    assert outcome.run_ids_archived == [kept]
    assert outcome.run_ids_excluded == [held] and outcome.holds == [ref]
    assert outcome.missing_run_ids == [gone]
    assert list(export_env.scope.run_ids) == [kept], "a held run never enters the archive"
    assert export_env.deletion["run_ids_excluded"] == [{"run_id": str(held), "holds": [ref.as_json()]}]
    assert export_env.deletion["missing_run_ids"] == [str(gone)]
    assert outcome.export_meta()["run_ids_excluded"] == [str(held)]


@pytest.mark.asyncio
async def test_a_signed_archive_verifies_with_the_deployment_key(export_env, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.services import compliance_signing

    private = Ed25519PrivateKey.generate()
    key = compliance_signing.SigningKey(
        key_id=compliance_signing.key_id_for(private.public_key()),
        public_key_pem=compliance_signing.public_key_pem(private.public_key()),
        private_key=private,
    )
    monkeypatch.setattr(compliance_signing, "active_signing_key", AsyncMock(return_value=key))
    monkeypatch.setattr(compliance_signing, "public_keys", AsyncMock(return_value={key.key_id: key.public_key_pem}))
    ids, job_id, storage = [uuid.uuid4()], uuid.uuid4(), MemStorage()

    outcome = await _export(_existing_db(ids), ids, storage, job_id=job_id)

    assert outcome.signing == {"state": "signed", "key_id": key.key_id}
    stored = storage.objects[(packs.COMPLIANCE_BUCKET, outcome.location)]
    from app.services.compliance_pack_verify import verify_archive_file
    result = verify_archive_file(io.BytesIO(stored), recorded_manifest_sha256=outcome.manifest_sha256,
                                 public_keys={key.key_id: key.public_key_pem}, pack_id=job_id,
                                 record_signature=outcome.signature, record_signing_key_id=key.key_id)
    assert (result["result"], result["signature"]) == ("verified", "valid")

    # A key the verifier does not know: the stored archive cannot be proven -> failed.
    monkeypatch.setattr(compliance_signing, "public_keys", AsyncMock(return_value={}))
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(_existing_db(ids), ids, MemStorage())
    assert caught.value.params == {"reason": "signature invalid"}


@pytest.mark.asyncio
async def test_stream_and_remove_never_leak_and_never_raise():
    storage = MemStorage({(packs.COMPLIANCE_BUCKET, "exports/k.zip"): b"abc" * 5000})
    chunks = [c async for c in svc.stream_archive(storage, key="exports/k.zip")]
    assert b"".join(chunks) == b"abc" * 5000

    class _Broken(MemStorage):
        async def delete_object(self, key, bucket=None):
            raise ConnectionError("down")

    assert await svc.remove_archive(_Broken(), key="exports/k.zip") is False
    assert await svc.remove_archive(storage, key="exports/k.zip") is True
    assert not storage.objects


@pytest.mark.asyncio
async def test_nothing_left_to_archive_is_a_build_failure(export_env):
    held = uuid.uuid4()
    export_env.holds = {held: [_hold()]}
    with pytest.raises(svc.ExportFailed) as caught:
        await _export(_existing_db([held]), [held], MemStorage())
    assert caught.value.code == "build"
    assert "no runs left to archive" in caught.value.message
