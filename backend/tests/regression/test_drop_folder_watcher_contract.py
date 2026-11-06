"""W34 STRAT-13 — the drop-folder watcher's structural promises, read from the source.

The behaviour is tested on fakes and on PostgreSQL; these pin what a later edit
could quietly undo without a behavioural test noticing at once:

* a listing never loads a whole bucket: the watcher's code calls exactly two
  storage methods, ``list_common_prefixes`` and ``head_object`` -- never
  ``list_objects`` (which pages EVERY object under a prefix into memory) and
  never one that reads, writes or deletes an object;
* a found sentinel takes the webhook's own path: both call
  ``ingest_test_run.delay`` with exactly ``sentinel_key=`` and ``minio_prefix=``
  (the same reader checks, Redis dedup and dead-letter queue);
* the watcher runs in the worker, never in the API: no router imports it, and
  the service stages only (no commit).
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
SERVICE = APP / "services" / "drop_folder_watcher.py"
TASKS = APP / "worker" / "tasks.py"
WEBHOOKS = APP / "routers" / "webhooks.py"
WATCHER_FUNCTIONS = {"_drop_folder_scan_prefix", "_drop_folder_watcher_sweep", "watch_drop_folders"}
STORAGE_METHODS = {
    "list_objects", "list_common_prefixes", "head_object", "get_object_content", "stream_object", "put_object",
    "put_object_stream", "get_presigned_url", "delete_object", "delete_prefix",
}


def _watcher_nodes() -> list[ast.AST]:
    tasks = ast.parse(TASKS.read_text(encoding="utf-8"))
    functions = [n for n in tasks.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name in WATCHER_FUNCTIONS]
    assert {f.name for f in functions} == WATCHER_FUNCTIONS
    return [ast.parse(SERVICE.read_text(encoding="utf-8")), *functions]


def _called_attributes(nodes: list[ast.AST]) -> set[str]:
    return {
        node.func.attr for tree in nodes for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _delay_keywords(tree: ast.AST) -> list[set[str]]:
    return [
        {kw.arg for kw in node.keywords if kw.arg}
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "delay"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "ingest_test_run"
    ]


def test_the_watcher_calls_only_the_folder_listing_and_head() -> None:
    used = _called_attributes(_watcher_nodes()) & STORAGE_METHODS
    assert used == {"list_common_prefixes", "head_object"}, (
        f"the drop-folder watcher calls {sorted(used)}: it may only list folders and HEAD a sentinel "
        "(list_objects would page a whole prefix of objects every minute)"
    )


def test_a_sentinel_is_queued_exactly_as_the_webhook_queues_it() -> None:
    webhook = _delay_keywords(ast.parse(WEBHOOKS.read_text(encoding="utf-8")))
    watcher = [kw for node in _watcher_nodes() for kw in _delay_keywords(node)]
    assert webhook == [{"sentinel_key", "minio_prefix"}]
    assert watcher == webhook


def test_the_watcher_is_worker_only_and_its_service_stages_only() -> None:
    importers = sorted(
        str(path.relative_to(APP)) for path in (APP / "routers").rglob("*.py")
        if "drop_folder_watcher" in path.read_text(encoding="utf-8")
    )
    assert importers == [], f"the API must not run the watcher: {importers}"
    assert ".commit(" not in SERVICE.read_text(encoding="utf-8")
