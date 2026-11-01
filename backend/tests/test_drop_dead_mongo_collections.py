"""W25d HYG-02 — ``backend/scripts/drop_dead_mongo_collections.py``, against a fake Mongo.

* Dry run (the default) reads names and counts and drops nothing.
* ``--apply`` reads EVERY count before the first drop: one non-empty collection → exit 2, a sentence naming
  each non-empty one with its count, and NOTHING dropped; all empty → each existing one dropped, absent ones
  skipped.
* It only ever lists, counts or drops the three dead names — never a live collection — and prints names and
  counts only (never a document).
* ``ensure_indexes`` no longer has a spec for any of the three (``create_index`` would re-create a dropped
  collection at the next start, contract §0.8).

The script is loaded the way ``python - < script`` runs it in the pod: compiled from text, not as __main__.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from app.db import mongo as app_mongo
from app.db.mongo import Collections

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drop_dead_mongo_collections.py"
DEAD = ("execution_logs", "ocp_pod_events", "raw_testng_xml")
#: Live collections the fake Mongo also holds, with documents a mistaken drop would destroy.
LIVE = {"raw_allure_json": 5, "run_summaries": 2, "ai_analysis_payloads": 1, "rest_api_payloads": 4}
#: A document body the script must never print.
DOC_SENTINEL = "zz-document-body-never-printed"


def _load_script() -> dict[str, Any]:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "__file__" not in text
    namespace: dict[str, Any] = {"__name__": "drop_dead_mongo_from_stdin"}
    exec(compile(text, "<stdin>", "exec"), namespace)              # noqa: S102 - our own script, read from the repo
    return namespace


class _FakeCollection:
    def __init__(self, db: "_FakeMongo", name: str) -> None:
        self.db, self.name = db, name

    async def count_documents(self, query: dict[str, Any]) -> int:
        self.db.calls.append(("count", self.name, query))
        return len(self.db.collections[self.name])

    async def find(self, *_args: Any, **_kwargs: Any) -> Any:   # pragma: no cover - must never be called
        raise AssertionError("the script read a document")


class _FakeMongo:
    """Collection name → list of documents; records every call with the collection it names."""

    def __init__(self, counts: dict[str, int]) -> None:
        self.collections = {name: [{"_id": i, "body": DOC_SENTINEL} for i in range(n)] for name, n in counts.items()}
        self.calls: list[tuple[Any, ...]] = []

    async def list_collection_names(self, filter: Optional[dict[str, Any]] = None) -> list[str]:  # noqa: A002
        self.calls.append(("list", filter))
        names = list(self.collections)
        if filter is not None:
            names = [n for n in names if n in filter["name"]["$in"]]
        return names

    def __getitem__(self, name: str) -> _FakeCollection:
        self.calls.append(("get", name))
        return _FakeCollection(self, name)

    async def drop_collection(self, name: str) -> None:
        self.calls.append(("drop", name))
        del self.collections[name]

    def named(self) -> set[str]:
        """Every collection name the script addressed (get / count / drop) or filtered on (list)."""
        out: set[str] = set()
        for call in self.calls:
            if call[0] == "list":
                out |= set(call[1]["name"]["$in"])
            else:
                out.add(call[1])
        return out

    def drops(self) -> list[str]:
        return [call[1] for call in self.calls if call[0] == "drop"]


def _run(db: _FakeMongo, *, apply: bool) -> tuple[int, dict[str, Any], Optional[str]]:
    return asyncio.run(_load_script()["run"](db, apply=apply))


def test_the_script_names_exactly_the_three_dead_collections_and_no_live_one():
    script = _load_script()
    assert script["DEAD_COLLECTIONS"] == DEAD
    live_names = {value for key, value in vars(Collections).items() if key.isupper()}
    assert len(live_names) >= 9, "fail-open: the live Collections constants were read"
    assert not set(DEAD) & live_names, "a dead name is still a Collections constant"
    text = SCRIPT.read_text(encoding="utf-8")
    assert not [name for name in live_names if name in text], "the script names a live collection"


def test_a_dry_run_reports_names_and_counts_and_drops_nothing():
    db = _FakeMongo({"execution_logs": 0, "ocp_pod_events": 0, "raw_testng_xml": 2, **LIVE})

    code, report, refusal = _run(db, apply=False)

    assert (code, refusal) == (0, None)
    assert report == {
        "execution_logs": {"exists": True, "documents": 0},
        "ocp_pod_events": {"exists": True, "documents": 0},
        "raw_testng_xml": {"exists": True, "documents": 2},
    }
    assert db.drops() == [] and set(db.collections) == set(DEAD) | set(LIVE)
    assert db.named() == set(DEAD)


def test_an_absent_collection_reports_not_existing_and_is_never_addressed():
    db = _FakeMongo({"ocp_pod_events": 0, **LIVE})

    _code, report, _refusal = _run(db, apply=False)

    assert report["execution_logs"] == {"exists": False, "documents": 0}
    assert report["raw_testng_xml"] == {"exists": False, "documents": 0}
    assert ("get", "execution_logs") not in db.calls and ("get", "raw_testng_xml") not in db.calls


def test_apply_with_one_non_empty_collection_refuses_and_drops_nothing():
    db = _FakeMongo({"execution_logs": 3, "ocp_pod_events": 0, "raw_testng_xml": 0, **LIVE})

    code, report, refusal = _run(db, apply=True)

    assert code == 2
    assert refusal == "refused: execution_logs has 3 documents — nothing was dropped."
    assert db.drops() == [], "one non-empty collection refuses the whole run"
    assert set(db.collections) == set(DEAD) | set(LIVE)
    assert [entry["dropped"] for entry in report.values()] == [False, False, False]
    assert report["execution_logs"] == {"exists": True, "documents": 3, "dropped": False}


def test_apply_names_every_non_empty_collection_with_its_count():
    db = _FakeMongo({"execution_logs": 0, "ocp_pod_events": 1, "raw_testng_xml": 12, **LIVE})

    code, _report, refusal = _run(db, apply=True)

    assert code == 2 and db.drops() == []
    assert refusal == "refused: ocp_pod_events has 1 document, raw_testng_xml has 12 documents — nothing was dropped."


def test_apply_with_all_empty_drops_each_existing_one_and_skips_the_absent():
    db = _FakeMongo({"execution_logs": 0, "raw_testng_xml": 0, **LIVE})

    code, report, refusal = _run(db, apply=True)

    assert (code, refusal) == (0, None)
    assert db.drops() == ["execution_logs", "raw_testng_xml"]
    assert report == {
        "execution_logs": {"exists": True, "documents": 0, "dropped": True},
        "ocp_pod_events": {"exists": False, "documents": 0, "dropped": False},
        "raw_testng_xml": {"exists": True, "documents": 0, "dropped": True},
    }
    assert set(db.collections) == set(LIVE), "every live collection is still there, documents included"
    assert {name: len(docs) for name, docs in db.collections.items()} == LIVE
    assert db.named() == set(DEAD)
    counted_before_first_drop = db.calls.index(("drop", "execution_logs"))
    assert [c for c in db.calls[counted_before_first_drop:] if c[0] == "count"] == []


@pytest.mark.parametrize(
    ("argv", "counts", "code"),
    [([], {"execution_logs": 4}, 0), (["--apply"], {"execution_logs": 4}, 2), (["--apply"], {"execution_logs": 0}, 0)],
    ids=["dry-run", "apply-refused", "apply-dropped"],
)
def test_main_prints_one_json_object_of_names_and_counts_only(monkeypatch, capsys, argv, counts, code):
    db = _FakeMongo({**counts, **LIVE})
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    monkeypatch.setattr(app_mongo, "get_mongo_db", lambda: db)
    monkeypatch.setattr(app_mongo, "close_mongo", close)

    assert asyncio.run(_load_script()["main"](argv)) == code

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1
    report = json.loads(lines[0])
    assert list(report) == list(DEAD)
    assert DOC_SENTINEL not in captured.out + captured.err
    assert not [name for name in LIVE if name in captured.out + captured.err]
    assert closed == [True]
    if code == 2:
        assert captured.err == "refused: execution_logs has 4 documents — nothing was dropped.\n"
        assert "execution_logs" in db.collections
    else:
        assert captured.err == ""


def test_an_unreachable_mongo_exits_1_with_the_exception_type_only(monkeypatch, capsys):
    class ServerSelectionTimeoutError(Exception):
        pass

    class _Down:
        async def list_collection_names(self, **_kwargs: Any) -> list[str]:
            raise ServerSelectionTimeoutError("mongodb://admin:zz-uri-password@mongo:27017 refused")

    async def close() -> None:
        return None

    monkeypatch.setattr(app_mongo, "get_mongo_db", lambda: _Down())
    monkeypatch.setattr(app_mongo, "close_mongo", close)

    assert asyncio.run(_load_script()["main"](["--apply"])) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "mongo unreachable: ServerSelectionTimeoutError\n"


def test_ensure_indexes_never_creates_a_dead_collection(monkeypatch):
    created: list[str] = []

    class _Coll:
        def __init__(self, name: str) -> None:
            self.name = name

        async def create_index(self, keys: Any, **kwargs: Any) -> str:
            created.append(self.name)
            return "ix"

    class _Db:
        def __getitem__(self, name: str) -> _Coll:
            return _Coll(name)

    monkeypatch.setattr(app_mongo, "get_mongo_db", lambda: _Db())

    asyncio.run(app_mongo.ensure_indexes())

    assert {"raw_allure_json", "run_summaries", "ai_analysis_payloads"} <= set(created), "fail-open: specs ran"
    assert not set(created) & set(DEAD), "create_index re-creates an absent collection at every start"
