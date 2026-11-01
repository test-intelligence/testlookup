"""Drop the three dead Mongo collections (W25d HYG-02) — an in-pod tool, never a startup side effect.

``execution_logs``, ``ocp_pod_events`` and ``raw_testng_xml`` never had a writer. They existed only because
``app.db.mongo.ensure_indexes`` created an index on each at every start, and ``create_index`` CREATES an absent
collection. W25d removed those index specs, so once EVERY pod of the new build is up, a dropped collection
stays dropped (run this after the rollout, never before).

Runs INSIDE the backend pod, fed on stdin, with the owner's approval for ``--apply``:

    kubectl exec -i <api-pod> -- env PYTHONPATH=/app python - < backend/scripts/drop_dead_mongo_collections.py
    kubectl exec -i <api-pod> -- env PYTHONPATH=/app python - --apply < backend/scripts/drop_dead_mongo_collections.py

DRY RUN by default: nothing is dropped. It reads ``settings.MONGO_URI`` / ``settings.MONGO_DB`` (through
``app.db.mongo.get_mongo_db``) and writes ONE JSON object to stdout, a key per dead collection:

    {"execution_logs": {"exists": true, "documents": 0}, "ocp_pod_events": {...}, "raw_testng_xml": {...}}

``--apply`` adds ``"dropped": true | false`` to each entry. It drops NOTHING when ANY of the three holds a
document (exit 2; a stderr line names every non-empty one with its count); otherwise it drops each one that
exists (an absent one is skipped) and exits 0. It only ever names, counts or drops the three literals below —
never another collection — and prints names and counts only, never a document. Exit 1: Mongo unreachable
(the exception TYPE on stderr).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Optional

#: Literals on purpose: the ``Collections`` constants for these names are gone (W25d HYG-02).
DEAD_COLLECTIONS = ("execution_logs", "ocp_pod_events", "raw_testng_xml")

EXIT_OK = 0
EXIT_UNREACHABLE = 1
EXIT_REFUSED = 2


async def survey(db: Any) -> dict[str, dict[str, Any]]:
    """``{name: {"exists": bool, "documents": int}}`` for the three, in ``DEAD_COLLECTIONS`` order (reads only)."""
    present = set(await db.list_collection_names(filter={"name": {"$in": list(DEAD_COLLECTIONS)}}))
    out: dict[str, dict[str, Any]] = {}
    for name in DEAD_COLLECTIONS:
        exists = name in present
        documents = int(await db[name].count_documents({})) if exists else 0
        out[name] = {"exists": exists, "documents": documents}
    return out


def refusal_sentence(non_empty: dict[str, int]) -> str:
    """'refused: execution_logs has 3 documents, raw_testng_xml has 1 document — nothing was dropped.'"""
    parts = [f"{name} has {count} document{'' if count == 1 else 's'}" for name, count in non_empty.items()]
    return "refused: " + ", ".join(parts) + " — nothing was dropped."


async def run(db: Any, *, apply: bool) -> tuple[int, dict[str, dict[str, Any]], Optional[str]]:
    """``(exit code, report, refusal sentence or None)``. With ``apply``, every count is read BEFORE the first
    drop: one non-empty collection refuses the whole run (nothing dropped)."""
    report = await survey(db)
    if not apply:
        return EXIT_OK, report, None
    non_empty = {name: entry["documents"] for name, entry in report.items() if entry["documents"] > 0}
    if non_empty:
        for entry in report.values():
            entry["dropped"] = False
        return EXIT_REFUSED, report, refusal_sentence(non_empty)
    for name in DEAD_COLLECTIONS:
        entry = report[name]
        if entry["exists"]:
            await db.drop_collection(name)
        entry["dropped"] = bool(entry["exists"])
    return EXIT_OK, report, None


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="drop_dead_mongo_collections")
    parser.add_argument(
        "--apply", action="store_true",
        help="drop them, only when every one is empty (default: dry run, nothing dropped)",
    )
    args = parser.parse_args(argv)

    from app.db.mongo import close_mongo, get_mongo_db

    try:
        code, report, refusal = await run(get_mongo_db(), apply=args.apply)
    except Exception as exc:  # noqa: BLE001 - an unreachable store: report the TYPE only, never the URI
        sys.stderr.write(f"mongo unreachable: {type(exc).__name__}\n")
        return EXIT_UNREACHABLE
    finally:
        await close_mongo()
    sys.stdout.write(json.dumps(report, sort_keys=False) + "\n")
    if refusal is not None:
        sys.stderr.write(refusal + "\n")
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
