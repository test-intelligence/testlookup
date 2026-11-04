"""W30 ADM-05 phase 2: the MongoDB documents of run history are classified and rewritten by ONE spec.

``services/project_export_documents.py`` is the review point for every future
run-scoped collection (contract §2.2): (D1) every ``Collections.*`` member and the
pipeline event log is exported or named excluded; (D2) the retention purge and
the export agree on what a run owns; (D3) every ``unique_key`` is a real unique
index; (D4) ``validate_document`` refuses each crafted shape and accepts a real
document of each collection; (D5) ``rewrite_document`` rewrites every id field
and returns None for an unresolved key; plus the writer-shape guard (open point
4): every top-level ``*_id`` field a writer sets is named by its spec. Pure: no
database, no Mongo client.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bson import ObjectId

from app.db import mongo as mongo_module
from app.db.mongo import Collections
from app.services import project_export_documents as d
from app.services import retention_service
from app.services.project_export_vocab import MONGO_DOC_MAX_BYTES, MONGO_MAX_DEPTH

APP = Path(__file__).resolve().parents[1] / "app"
_FIX = "classify it in project_export_documents.py (MONGO_COLLECTIONS with its MongoSpec, or MONGO_EXCLUDED with why)"


def _collections_members() -> set[str]:
    return {v for k, v in vars(Collections).items() if k.isupper() and isinstance(v, str)}


# ── (D1) every collection is classified ──────────────────────────────────────

def test_d1_every_collection_is_exported_or_named_excluded():
    exported = [s.collection for s in d.MONGO_COLLECTIONS]
    assert len(exported) == len(set(exported)), "a collection appears twice in MONGO_COLLECTIONS"
    assert not set(exported) & set(d.MONGO_EXCLUDED), "a collection is both exported and excluded"
    expected = _collections_members() | {retention_service._PIPELINE_EVENT_LOG}
    unclassified = sorted(expected - set(exported) - set(d.MONGO_EXCLUDED))
    assert not unclassified, "\n".join(f"{c}: {_FIX}" for c in unclassified)
    unknown = sorted(set(exported) - expected)
    assert not unknown, f"{unknown} are not Collections members nor the pipeline event log"
    assert all(why.strip() for why in d.MONGO_EXCLUDED.values())
    assert d.PIPELINE_EVENT_LOG == retention_service._PIPELINE_EVENT_LOG
    assert len(d.MONGO_COLLECTIONS) == 10


# ── (D2) the purge and the export agree on what a run owns ───────────────────

def test_d2_every_purge_plan_collection_is_exported_with_the_same_key():
    for collection, key, _attr in retention_service._MONGO_PLANS:
        spec = d.MONGO_SPECS.get(collection)
        assert spec is not None, f"{collection} is purged with its runs but not exported: {_FIX}"
        assert spec.key == key, f"{collection}: the purge selects by {key!r}, the export by {spec.key!r}"
    assert {c for c, _k, _a in retention_service._MONGO_PLANS} == set(d.MONGO_SPECS)


def test_d2_key_maps_and_remap_targets_are_history_tables():
    from app.services.project_export_entities import HISTORY_TABLES

    for spec in d.MONGO_COLLECTIONS:
        assert spec.key_map in HISTORY_TABLES, spec.collection
        assert set(spec.id_fields.values()) | set(spec.added_fields.values()) <= set(HISTORY_TABLES), spec.collection
        assert spec.key not in spec.id_fields and spec.key not in spec.kept_ids, spec.collection
        assert (spec.key_map == "test_runs") or not spec.run_fields, f"{spec.collection}: run_fields need a run key"
    assert [s.collection for s in d.MONGO_COLLECTIONS if s.added_fields] == [Collections.AI_ANALYSIS_PAYLOADS]
    assert [s.collection for s in d.MONGO_COLLECTIONS if s.slug_key] == [Collections.LIVE_EXECUTION_EVENTS]


# ── (D3) every unique_key is a real unique index ─────────────────────────────

class _RecordingCollection:
    def __init__(self, name: str, calls: list) -> None:
        self.name, self.calls = name, calls

    async def create_index(self, keys, **kwargs):
        self.calls.append((self.name, tuple(k for k, _ in keys), kwargs))


class _RecordingDb:
    def __init__(self) -> None:
        self.calls: list = []

    def __getitem__(self, name: str) -> _RecordingCollection:
        return _RecordingCollection(name, self.calls)


async def test_d3_every_unique_key_equals_a_unique_index_of_ensure_indexes(monkeypatch):
    db = _RecordingDb()
    monkeypatch.setattr(mongo_module, "get_mongo_db", lambda: db)
    await mongo_module.ensure_indexes()
    unique = {(name, keys) for name, keys, kwargs in db.calls if kwargs.get("unique")}
    assert unique, "ensure_indexes declared no unique index — the recorder saw nothing"
    for spec in d.MONGO_COLLECTIONS:
        if spec.unique_key:
            assert (spec.collection, spec.unique_key) in unique, (
                f"{spec.collection}.unique_key {spec.unique_key} is not a unique index in app/db/mongo.ensure_indexes"
            )
    # a collection with a non-partial single unique index names one of them (the rest fail the insert itself)
    for name, keys in unique:
        if name in d.MONGO_SPECS:
            assert d.MONGO_SPECS[name].unique_key, f"{name} has the unique index {keys}: name one as unique_key"


# ── canonical run ids, the encoding ──────────────────────────────────────────

def test_canonical_run_uuid_matches_the_live_ingest_helper():
    from app.services.stream_service import canonical_test_run_uuid

    run = str(uuid.uuid4())
    for value in (run, run.upper(), "local-abc12345", "build-42"):
        assert d.canonical_run_uuid(value) == canonical_test_run_uuid(value), value


def test_a_document_round_trips_with_its_stored_types():
    when = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
    oid = ObjectId()
    doc = {"test_run_id": str(uuid.uuid4()), "at": when, "ref": oid, "n": 3, "x": 1.5, "nested": {"list": [1, "a"]}}
    line = d.encode_document(doc)
    assert "\n" not in line and '"$date"' in line and '"$oid"' in line
    back = d.decode_document(line)
    assert back["at"] == when and back["ref"] == oid and back["nested"] == {"list": [1, "a"]}
    assert d.encode_document(back) == line


def test_an_unsupported_stored_type_refuses_the_encoding():
    from bson import Binary, Decimal128

    for value in (Binary(b"q"), Decimal128("1.5")):
        with pytest.raises(d.UnsupportedDocumentType):
            d.encode_document({"test_run_id": str(uuid.uuid4()), "v": value})


# ── (D4) validation before any write ─────────────────────────────────────────

def _ids() -> dict[str, str]:
    return {k: str(uuid.uuid4()) for k in ("run", "case", "pipeline", "project")}


def _real_documents(ids: dict[str, str]) -> dict[str, dict]:
    """One document per collection in the shape its writer stores (no _id: the export removes it)."""
    now = datetime.now(timezone.utc)
    run, case, pipe, project = ids["run"], ids["case"], ids["pipeline"], ids["project"]
    return {
        Collections.RAW_ALLURE_JSON: {"test_case_id": "allure-uuid-qx", "test_run_id": run, "test_name": "t",
                                      "raw_result": {"statusDetails": {"message": "boom"}, "steps": []},
                                      "updated_at": now},
        Collections.REST_API_PAYLOADS: {"v": 1, "project_id": project, "test_run_id": run, "build_number": "b-1",
                                        "run_created_at": now, "test_case_id": case, "seq": 0,
                                        "request": {"method": "GET", "headers": {"Authorization": "[REDACTED]"}},
                                        "response": {"status": 200}},
        Collections.LIVE_EXECUTION_EVENTS: {"run_id": "local-abc12345", "session_id": "local-abc12345",
                                            "event_type": "test_result", "seq": 4, "project_id": project},
        Collections.RUN_SUMMARIES: {"test_run_id": run, "project_id": project, "build_number": "b-1",
                                    "summary": "two failures", "generated_at": now},
        Collections.AI_RUN_VERSIONS: {"version_id": str(uuid.uuid4()), "test_run_id": run, "project_id": project,
                                      "kind": "run_summary", "version": 1, "pipeline_run_id": pipe,
                                      "content_sha256": "c" * 64, "generated_at": now, "payload": {"text": "x"}},
        Collections.DECISION_EVIDENCE_SNAPSHOTS: {"project_id": project, "test_run_id": run, "pipeline_run_id": pipe,
                                                  "schema_version": 3, "content_sha256": "d" * 64, "created_at": now},
        Collections.AI_ANALYSIS_PAYLOADS: {"test_case_id": case, "prompt": "p", "analysis": {"root": "q"},
                                           "intermediate_steps": [], "updated_at": now},
        d.PIPELINE_EVENT_LOG: {"pipeline_run_id": pipe, "event_type": "stage_completed", "timestamp": now,
                               "stage_name": "triage", "test_case_id": case, "detail": {}},
        Collections.DECISION_REPORTS: {"report_id": str(uuid.uuid4()), "project_id": project, "test_run_id": run,
                                       "pipeline_run_id": pipe, "report_version": 1, "status": "published",
                                       "generated_at": now, "decision_intelligence": {}, "verification": {},
                                       "markdown_report": "# r"},
        Collections.DECISION_REPORT_ATTEMPTS: {"attempt_id": str(uuid.uuid4()), "project_id": project,
                                               "test_run_id": run, "pipeline_run_id": pipe, "status": "failed",
                                               "attempted_at": now, "reason": "r", "verification": {}},
    }


def test_d4_a_real_document_of_each_collection_is_valid():
    docs = _real_documents(_ids())
    assert set(docs) == set(d.MONGO_SPECS)
    for collection, doc in docs.items():
        spec = d.MONGO_SPECS[collection]
        assert d.validate_document(doc, spec) is None, collection
        # and after the archive round trip (Extended JSON) too
        assert d.validate_document(d.decode_document(d.encode_document(doc)), spec) is None, collection


def _nested(depth: int) -> dict:
    doc: dict = {"leaf": 1}
    for _ in range(depth - 1):
        doc = {"n": doc}
    return doc


def test_d4_each_crafted_document_is_refused_with_its_reason():
    spec = d.MONGO_SPECS[Collections.RUN_SUMMARIES]
    run = str(uuid.uuid4())
    assert d.validate_document([{"test_run_id": run}], spec) == "not_an_object"
    assert d.validate_document({"test_run_id": run, "$where": "sleep(1)"}, spec) == "operator_key"
    assert d.validate_document({"test_run_id": run, "x": [{"$gt": 1}]}, spec) == "operator_key"
    assert d.validate_document({"test_run_id": run, "a.b": 1}, spec) == "dotted_key"
    assert d.validate_document({"test_run_id": run, "x": {"a\x00b": 1}}, spec) == "dotted_key"
    assert d.validate_document({"test_run_id": run, "deep": _nested(MONGO_MAX_DEPTH)}, spec) == "too_deep"
    assert d.validate_document({"test_run_id": run, "ok": _nested(MONGO_MAX_DEPTH - 1)}, spec) is None
    big = {"test_run_id": run, "blob": "q" * MONGO_DOC_MAX_BYTES}
    assert d.validate_document(big, spec) == "too_large"
    assert d.validate_document({"test_run_id": run}, spec, encoded_size=MONGO_DOC_MAX_BYTES + 1) == "too_large"
    assert d.validate_document({"summary": "no key"}, spec) == "missing_key"
    assert d.validate_document({"test_run_id": "not-a-uuid"}, spec) == "missing_key"
    assert d.validate_document({"test_run_id": run, "_id": "forged"}, spec) == "id_present"
    assert set(d.DOCUMENT_REASONS) >= {"not_an_object", "operator_key", "dotted_key", "too_deep", "too_large",
                                       "missing_key", "id_present", "duplicate_key", "unsupported_type"}


def test_d4_a_forged_extended_json_type_is_refused():
    spec = d.MONGO_SPECS[Collections.RUN_SUMMARIES]
    line = '{"test_run_id": "%s", "v": {"$binary": {"base64": "cQ==", "subType": "00"}}}' % uuid.uuid4()
    assert d.validate_document(d.decode_document(line), spec) == "unsupported_type"


def test_d4_a_repeated_unique_key_is_detectable_inside_the_archive():
    ids = _ids()
    doc = _real_documents(ids)[Collections.DECISION_REPORTS]
    spec = d.MONGO_SPECS[Collections.DECISION_REPORTS]
    assert d.unique_value(spec, doc) == d.unique_value(spec, dict(doc, markdown_report="other"))
    assert d.unique_value(spec, doc) != d.unique_value(spec, dict(doc, report_version=2))
    assert d.unique_value(d.MONGO_SPECS[Collections.RAW_ALLURE_JSON], doc) is None


# ── (D5) the rewrite ─────────────────────────────────────────────────────────

def _remaps(ids: dict[str, str], new: dict[str, str]) -> dict:
    live_run = str(d.canonical_run_uuid("local-abc12345"))
    return {
        "test_runs": {ids["run"]: new["run"], live_run: new["live_run"]},
        "test_cases": lambda s: new["case"] if s == ids["case"] else None,   # a function form is accepted too
        "agent_pipeline_runs": {ids["pipeline"]: new["pipeline"]},
    }


def test_d5_every_id_field_is_rewritten_and_nothing_else():
    ids, new = _ids(), {k: str(uuid.uuid4()) for k in ("run", "live_run", "case", "pipeline", "project")}
    source_ids = set(ids.values())
    for collection, doc in _real_documents(ids).items():
        spec = d.MONGO_SPECS[collection]
        out = d.rewrite_document(spec, dict(doc, _id=ObjectId()), remaps=_remaps(ids, new),
                                 new_project_id=new["project"], new_run_id=new["run"])
        assert out is not None, collection
        assert "_id" not in out, f"{collection}: the import never chooses an _id"
        rewritten = [spec.key, *spec.id_fields, *spec.run_fields, *spec.added_fields]
        if spec.project_field:
            rewritten.append(spec.project_field)
        for name in rewritten:
            assert out.get(name) not in source_ids, f"{collection}.{name} still carries a source id"
        if spec.project_field and spec.project_field in doc:
            assert out[spec.project_field] == new["project"], collection
        for name, value in doc.items():
            if name not in rewritten:
                assert out[name] == value, f"{collection}.{name} must stay verbatim"
    live = d.rewrite_document(d.MONGO_SPECS[Collections.LIVE_EXECUTION_EVENTS],
                              _real_documents(ids)[Collections.LIVE_EXECUTION_EVENTS],
                              remaps=_remaps(ids, new), new_project_id=new["project"])
    assert live is not None and live["run_id"] == live["session_id"] == new["live_run"]
    payload = d.rewrite_document(d.MONGO_SPECS[Collections.AI_ANALYSIS_PAYLOADS],
                                 _real_documents(ids)[Collections.AI_ANALYSIS_PAYLOADS],
                                 remaps=_remaps(ids, new), new_project_id=new["project"], new_run_id=new["run"])
    assert payload is not None and payload["test_run_id"] == new["run"] and payload["test_case_id"] == new["case"]


def test_d5_an_unresolved_key_returns_none_and_an_unresolved_id_field_is_nulled():
    ids, new = _ids(), {k: str(uuid.uuid4()) for k in ("run", "live_run", "case", "pipeline", "project")}
    other = _ids()
    for collection, doc in _real_documents(other).items():
        if collection == Collections.LIVE_EXECUTION_EVENTS:
            doc = dict(doc, run_id="another-slug")
        out = d.rewrite_document(d.MONGO_SPECS[collection], doc, remaps=_remaps(ids, new),
                                 new_project_id=new["project"], new_run_id=new["run"])
        assert out is None, f"{collection}: a key outside the archive must not be inserted"
    version = dict(_real_documents(ids)[Collections.AI_RUN_VERSIONS], pipeline_run_id=str(uuid.uuid4()))
    out = d.rewrite_document(d.MONGO_SPECS[Collections.AI_RUN_VERSIONS], version, remaps=_remaps(ids, new),
                             new_project_id=new["project"])
    assert out is not None and out["pipeline_run_id"] is None and out["test_run_id"] == new["run"]
    with pytest.raises(ValueError):
        d.rewrite_document(d.MONGO_SPECS[Collections.AI_ANALYSIS_PAYLOADS],
                           _real_documents(ids)[Collections.AI_ANALYSIS_PAYLOADS], remaps=_remaps(ids, new),
                           new_project_id=new["project"])


# ── writer shapes (open point 4): every top-level *_id a writer sets is named ─

#: collection → (writer file under app/, marker keys a written dict literal carries). Every dict literal of that
#: file whose string keys include ALL the markers is a document (or its $set) as the writer stores it.
_WRITERS = {
    d.PIPELINE_EVENT_LOG: ("services/pipeline_event_log.py", {"pipeline_run_id", "event_type", "detail"}),
    Collections.RAW_ALLURE_JSON: ("services/ingestion.py", {"test_case_id", "test_run_id", "raw_result"}),
    Collections.AI_ANALYSIS_PAYLOADS: ("services/agent.py", {"test_case_id", "prompt", "analysis"}),
    Collections.AI_RUN_VERSIONS: ("services/ai_history_service.py", {"version_id", "test_run_id", "kind", "payload"}),
    Collections.REST_API_PAYLOADS: ("services/http_exchange_store.py", {"v", "project_id", "test_run_id", "run_created_at"}),
    Collections.RUN_SUMMARIES: ("agents/summary_agent.py", {"test_run_id", "project_id", "build_number"}),
    Collections.DECISION_EVIDENCE_SNAPSHOTS: ("services/decision_evidence_snapshot.py",
                                              {"test_run_id", "pipeline_run_id", "project_id"}),
}


def _written_keys(relative: str, markers: set[str]) -> set[str]:
    tree = ast.parse((APP / relative).read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            literal = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if markers <= literal:
                keys |= literal
    return keys


def _named(spec: d.MongoSpec) -> set[str]:
    named = {spec.key, *spec.id_fields, *spec.run_fields, *spec.added_fields, *spec.kept_ids}
    return named | ({spec.project_field} if spec.project_field else set())


@pytest.mark.parametrize("collection", sorted(_WRITERS))
def test_every_top_level_id_field_a_writer_sets_is_named_by_its_spec(collection):
    relative, markers = _WRITERS[collection]
    keys = _written_keys(relative, markers)
    assert keys, f"no dict literal in app/{relative} carries {sorted(markers)} — the writer moved: re-trace it"
    if collection == Collections.REST_API_PAYLOADS:
        keys.add("test_case_id")   # added per case by write_exchanges (base={**base, "test_case_id": case_id})
    spec = d.MONGO_SPECS[collection]
    unnamed = sorted(k for k in keys if k.endswith("_id") and k != "_id" and k not in _named(spec))
    assert not unnamed, (
        f"the {collection} writer sets {unnamed}: name each in its MongoSpec (id_fields / project_field / "
        "run_fields / kept_ids) in project_export_documents.py"
    )


def test_every_top_level_id_field_of_the_decision_report_models_is_named():
    from app.services.decision_report_service import DecisionReportAttemptV1, DecisionReportV1

    for model, collection in ((DecisionReportV1, Collections.DECISION_REPORTS),
                              (DecisionReportAttemptV1, Collections.DECISION_REPORT_ATTEMPTS)):
        fields = set(model.model_fields)
        unnamed = sorted(f for f in fields if f.endswith("_id") and f not in _named(d.MONGO_SPECS[collection]))
        assert not unnamed, f"{model.__name__} has {unnamed}: name each in the {collection} MongoSpec"


def test_the_pipeline_event_log_writer_is_the_one_traced():
    from app.services import pipeline_event_log

    source = inspect.getsource(pipeline_event_log.emit_event)
    assert '"pipeline_run_id": pipeline_run_id' in source and '"test_case_id": test_case_id' in source
    assert pipeline_event_log._COLLECTION == d.PIPELINE_EVENT_LOG
