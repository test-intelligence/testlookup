"""Reference warnings for a deletion of test runs (W22a ACT-04, contract §2.2 F-3).

Deleting a ``test_runs`` row silently NULLs every column that points at it with
``ON DELETE SET NULL`` — a later run loses its baseline link, a compliance pack
loses its run. Those breaks are WARNINGS (owner default): the preview lists
them, the executor records them on the deletion job, nothing is blocked.

The list of such columns is DERIVED from ``Base.metadata`` (every FK whose
target table is ``test_runs`` and whose ``ondelete`` is ``SET NULL``), never
hand-written, so a new SET NULL column is counted the day its model lands.

Only references that SURVIVE the deletion count (D-18): a referencing row that
the same deletion cascades away (its own ``run_id`` CASCADE, depth 1, or a
CASCADE to a table that itself CASCADEs to ``test_runs``, depth 2) breaks
nothing. Those chains come from the same metadata walk.

Fail SOFT: the counts run inside a SAVEPOINT (``db.begin_nested()``); any
failure answers ``ReferenceReport(measured=False)`` and logs one warning — a
warning must never fail or block a deletion. Commit-free (``STAGE_ONLY_SERVICES``)
and it never records activity.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional, Sequence

import structlog
from sqlalchemy import MetaData, any_, bindparam, column, exists, func, not_, or_, select, table
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

import app.models.postgres  # noqa: F401 — registers every table on Base.metadata before the walk
from app.db.postgres import Base
from app.services.retention_vocab import (
    REFERENCE_FALLBACK_LABEL,
    REFERENCE_FALLBACK_LABEL_ONE,
    REFERENCE_LABELS,
    REFERENCE_LABELS_ONE,
)

_slog = structlog.get_logger(__name__)

RUNS_TABLE = "test_runs"

#: One hop of a CASCADE chain: ("<table>.<fk column>", "<referenced table>.<column>").
Hop = tuple[str, str]


@dataclass(frozen=True)
class SetNullRef:
    """One ``ON DELETE SET NULL`` column pointing at ``test_runs``.

    ``survive_chains`` are the referencing table's CASCADE paths to
    ``test_runs`` (depth 1: ``(("run_baselines.run_id", "test_runs.id"),)``;
    depth 2: ``(("t.k1", "x.id"), ("x.k2", "test_runs.id"))``). A row reached
    by any chain from a deleted run is deleted with it and is not counted.
    """

    table: str
    column: str
    survive_chains: tuple[tuple[Hop, ...], ...] = ()

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


def _is(ondelete: Optional[str], action: str) -> bool:
    return (ondelete or "").upper() == action


def _cascade_chains(tbl: Any) -> tuple[tuple[Hop, ...], ...]:
    chains: list[tuple[Hop, ...]] = []
    if tbl.name == RUNS_TABLE:
        # A self-reference: the referencing row IS a run; it survives unless
        # its own id is deleted (a depth-1 hop from its primary key to itself).
        for pk in tbl.primary_key.columns:
            chains.append(((f"{tbl.name}.{pk.name}", f"{tbl.name}.{pk.name}"),))
    for fk1 in sorted(tbl.foreign_keys, key=lambda f: (f.parent.name, f.target_fullname)):
        if not _is(fk1.ondelete, "CASCADE"):
            continue
        target = fk1.column.table
        hop1: Hop = (f"{tbl.name}.{fk1.parent.name}", f"{target.name}.{fk1.column.name}")
        if target.name == RUNS_TABLE:
            chains.append((hop1,))
            continue
        for fk2 in sorted(target.foreign_keys, key=lambda f: (f.parent.name, f.target_fullname)):
            if _is(fk2.ondelete, "CASCADE") and fk2.column.table.name == RUNS_TABLE:
                hop2: Hop = (f"{target.name}.{fk2.parent.name}", f"{RUNS_TABLE}.{fk2.column.name}")
                chains.append((hop1, hop2))
    return tuple(dict.fromkeys(chains))


@lru_cache(maxsize=8)
def _derive(metadata: MetaData) -> tuple[SetNullRef, ...]:
    refs: list[SetNullRef] = []
    for tbl in metadata.sorted_tables:
        for fk in tbl.foreign_keys:
            if fk.column.table.name == RUNS_TABLE and _is(fk.ondelete, "SET NULL"):
                refs.append(SetNullRef(tbl.name, fk.parent.name, _cascade_chains(tbl)))
    unique = {(r.table, r.column): r for r in refs}
    return tuple(unique[k] for k in sorted(unique))


def set_null_refs(metadata: Optional[MetaData] = None) -> tuple[SetNullRef, ...]:
    """Every SET NULL FK to ``test_runs`` in ``metadata`` (default ``Base.metadata``),
    sorted by (table, column); derived once per metadata object."""
    return _derive(metadata if metadata is not None else Base.metadata)


def label_for(table_name: str, column_name: str, count: int) -> str:
    one = count == 1  # "1 compliance pack loses its run link", not "1 compliance packs lose their ..."
    template = (REFERENCE_LABELS_ONE if one else REFERENCE_LABELS).get(f"{table_name}.{column_name}")
    if template is not None:
        return template.format(count=count)
    fallback = REFERENCE_FALLBACK_LABEL_ONE if one else REFERENCE_FALLBACK_LABEL
    return fallback.format(count=count, table=table_name, column=column_name)


@dataclass(frozen=True)
class ReferenceReport:
    measured: bool
    items: list[dict] = field(default_factory=list)   # {"table","column","count","label"}, count > 0, count desc

    def job_record(self) -> Optional[dict]:
        """The ``DeletionJob.references_broken`` value: a FLAT dict of ints.

        None when measured and nothing broke; ``{"measured": False}`` when the
        count failed; else ``{"measured": True, "total": n, "<table>.<column>": k, …}``.
        """
        if not self.measured:
            return {"measured": False}
        if not self.items:
            return None
        record: dict[str, Any] = {"measured": True, "total": sum(int(i["count"]) for i in self.items)}
        for item in self.items:
            record[f"{item['table']}.{item['column']}"] = int(item["count"])
        return record


def _col(qualified: str) -> str:
    return qualified.split(".", 1)[1]


def count_statement(ref: SetNullRef, ids_param: Any) -> Any:
    """``SELECT count(*)`` of the rows of ``ref.table`` that point at a deleted
    run through ``ref.column`` AND survive the deletion (every CASCADE chain
    misses). ``ids_param`` is ONE ``uuid[]`` bind (``= ANY(:ids)``)."""
    names = {ref.column}
    for chain in ref.survive_chains:
        names.add(_col(chain[0][0]))
    t = table(ref.table, *[column(n) for n in sorted(names)])
    conds: list[Any] = [t.c[ref.column] == any_(ids_param)]
    for index, chain in enumerate(ref.survive_chains):
        src = t.c[_col(chain[0][0])]
        if len(chain) == 1:
            # NULL-safe: a bare NOT (k = ANY(:ids)) is NULL for a NULL k and
            # would drop a surviving row from the count.
            conds.append(or_(src.is_(None), not_(src == any_(ids_param))))
            continue
        (_, target), (x_src, _) = chain[0], chain[1]
        x_table, x_ref = target.split(".", 1)
        x_k2 = _col(x_src)
        x = table(x_table, column(x_ref), column(x_k2)).alias(f"cascade_{index}")
        conds.append(~exists().where(x.c[x_ref] == src, x.c[x_k2] == any_(ids_param)))
    return select(func.count()).select_from(t).where(*conds)


def _ids_param(run_ids: Sequence[uuid.UUID]) -> Any:
    return bindparam("purged_run_ids", value=list(run_ids), type_=ARRAY(PG_UUID(as_uuid=True)))


async def references_broken(
    db: AsyncSession,
    run_ids: Sequence[uuid.UUID],
    *,
    refs: Optional[Sequence[SetNullRef]] = None,
) -> ReferenceReport:
    """Count, per SET NULL reference, the surviving rows that deleting
    ``run_ids`` would un-link. Empty ``run_ids`` → measured, no statement.

    Runs inside a SAVEPOINT and fails SOFT: any exception (a fake without
    ``begin_nested``, a statement error) → ``ReferenceReport(False, [])`` and
    one ``retention_references_unmeasured`` warning; the caller's transaction
    stays usable and the deletion proceeds.
    """
    ids = list(dict.fromkeys(uuid.UUID(str(r)) for r in run_ids))
    if not ids:
        return ReferenceReport(True, [])
    wanted = tuple(refs) if refs is not None else set_null_refs()
    counted: list[tuple[SetNullRef, int]] = []
    try:
        async with db.begin_nested():
            for ref in wanted:
                count = (await db.execute(count_statement(ref, _ids_param(ids)))).scalar_one()
                counted.append((ref, int(count or 0)))
    except Exception as exc:  # noqa: BLE001 — fail soft: a warning never blocks a deletion
        _slog.warning(
            "retention_references_unmeasured",
            error_type=type(exc).__name__,
            refs=len(wanted),
            run_ids=len(ids),
        )
        return ReferenceReport(False, [])
    items: list[dict[str, Any]] = [
        {"table": ref.table, "column": ref.column, "count": n, "label": label_for(ref.table, ref.column, n)}
        for ref, n in counted
        if n > 0
    ]
    items.sort(key=lambda i: (-int(i["count"]), str(i["table"]), str(i["column"])))
    return ReferenceReport(True, items)
