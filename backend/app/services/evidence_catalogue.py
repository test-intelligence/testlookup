"""Server-owned evidence catalogue for grounded narrative claims (F-3 / G.2).

The problem this replaces
------------------------
``summary_assembler.extract_citations`` attached an evidence item only when the
first 40 characters of its excerpt appeared **verbatim** in generated prose, and
it was applied to layer 3 alone. A model that paraphrases -- which is the point
of a summary -- produced an empty citation list, so the three layers a reader
actually acts on (executive summary, incident view, action plan) were
structurally uncitable, and the one layer that could be cited only was when the
model happened to copy text.

The contract
------------
1. The server builds a numbered catalogue from evidence it already holds and
   renders it into the prompt as ``[E1] (source, test) excerpt``.
2. Each layer returns ``evidence_ids`` alongside its content.
3. The server resolves those ids **against the catalogue it built**. Anything
   that does not resolve is dropped and counted.

The load-bearing property is that a model id is never authoritative: it is a
token that must match one the server generated in this run. A fabricated
``E99`` resolves to nothing and is dropped, exactly as a fabricated URI would
be. This mirrors ``tools/recall_memory``, where identity comes from a
ContextVar and the model's input is treated as free text only.

Pure and total: no database, no LLM, no outbound calls, and no input shape
raises. A malformed layer payload loses its citations, never the report.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# How many evidence items the model is shown. Bounded because the catalogue is
# rendered into every layer prompt, and a context that is mostly catalogue
# crowds out the analysis it is meant to support.
CATALOGUE_LIMIT = 12
# Per-test cap, so one noisy test cannot fill the catalogue and starve the rest.
PER_TEST_LIMIT = 2
_EXCERPT_LIMIT = 150
_ID_PREFIX = "E"


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value)[:limit].strip()


def build_evidence_catalogue(
    analyses: Any,
    *,
    limit: int = CATALOGUE_LIMIT,
    per_test_limit: int = PER_TEST_LIMIT,
) -> list[dict[str, Any]]:
    """Assign stable ids to the evidence this run actually holds.

    Ordering is deterministic (analyses sorted by id) so ``E3`` denotes the same
    excerpt in every layer call of a run -- the three layer prompts are separate
    invocations, and an id that drifted between them would silently mis-cite.
    """
    catalogue: list[dict[str, Any]] = []
    analyses = _as_dict(analyses)
    for test_id in sorted(analyses, key=str):
        analysis = _as_dict(analyses[test_id])
        taken = 0
        for ref in _as_list(analysis.get("evidence_references")):
            if len(catalogue) >= max(0, limit):
                return catalogue
            if taken >= max(0, per_test_limit):
                break
            ref = _as_dict(ref)
            excerpt = _text(ref.get("excerpt"), _EXCERPT_LIMIT)
            if not excerpt:
                continue
            taken += 1
            catalogue.append({
                "ev_id": f"{_ID_PREFIX}{len(catalogue) + 1}",
                "source": _text(ref.get("source"), 60) or "unknown",
                "kind": _text(ref.get("kind"), 60) or "evidence",
                "excerpt": excerpt,
                # ``test_id`` is the key ``agents/consistency.py`` checks against
                # the analysed universe; it comes from the server's own mapping,
                # never from the model.
                "test_id": str(test_id),
                "test_name": _text(analysis.get("test_name"), 120),
            })
    return catalogue


def render_catalogue(catalogue: Iterable[Any]) -> str:
    """The block the model sees. Empty catalogue renders to an empty string.

    Rendering a header with no items under it invites the model to invent ids
    to fill it, which is the failure this whole mechanism exists to prevent.
    """
    lines = []
    for entry in catalogue:
        entry = _as_dict(entry)
        ev_id = entry.get("ev_id")
        if not ev_id:
            continue
        where = entry.get("test_name") or entry.get("test_id") or "?"
        lines.append(
            f"  [{ev_id}] ({entry.get('source', '?')}, {where}) {entry.get('excerpt', '')}"
        )
    if not lines:
        return ""
    return (
        "\n\nEvidence — cite these ids in evidence_ids. "
        "Use ONLY ids from this list; omit the field if nothing here supports "
        "the point:\n" + "\n".join(lines)
    )


def resolve_evidence_ids(
    raw_ids: Any, catalogue: Iterable[Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve model-supplied ids against the server-built catalogue.

    Returns ``(citations, unresolved)``. Ids are matched case-insensitively and
    de-duplicated in first-seen order. Anything absent from the catalogue is
    returned as unresolved so the caller can COUNT it -- a silently dropped
    fabrication and a genuine absence of evidence look identical otherwise.
    """
    index: dict[str, dict[str, Any]] = {}
    for entry in catalogue:
        entry = _as_dict(entry)
        ev_id = str(entry.get("ev_id") or "").strip().upper()
        if ev_id:
            index.setdefault(ev_id, entry)

    citations: list[dict[str, Any]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for raw in _as_list(raw_ids):
        key = str(raw).strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        entry = index.get(key)
        if entry is None:
            unresolved.append(str(raw)[:40])
            continue
        citations.append({
            "ev_id": entry["ev_id"],
            "source": entry.get("source"),
            "excerpt": entry.get("excerpt"),
            "test_id": entry.get("test_id"),
        })
    return citations, unresolved


def ground_layer(
    payload: Any, catalogue: Iterable[Any], *, layer_name: str = "unknown"
) -> dict[str, Any]:
    """Replace a layer's raw ``evidence_ids`` with resolved citations.

    Mutates nothing: returns a new payload plus a grounding record. A layer that
    cites nothing is reported as ``cited=False`` rather than being treated as an
    error -- "no evidence supports this point" is a legitimate answer, and the
    coverage metric is what makes its frequency visible.
    """
    catalogue = list(catalogue)
    payload = _as_dict(payload)
    raw_ids = payload.get("evidence_ids")
    citations, unresolved = resolve_evidence_ids(raw_ids, catalogue)

    grounded = dict(payload)
    grounded["citations"] = citations
    grounded.pop("evidence_ids", None)
    return {
        "payload": grounded,
        "grounding": {
            "layer": layer_name,
            "claimed_ids": len(_as_list(raw_ids)),
            "resolved": len(citations),
            "unresolved": unresolved,
            "cited": bool(citations),
            "catalogue_size": len(catalogue),
        },
    }


def coverage_summary(records: Iterable[Any]) -> dict[str, Any]:
    """Roll per-layer grounding records into the numbers the baseline reads."""
    records = [_as_dict(r) for r in records]
    layers = len(records)
    cited = sum(1 for r in records if r.get("cited"))
    claimed = sum(int(r.get("claimed_ids") or 0) for r in records)
    resolved = sum(int(r.get("resolved") or 0) for r in records)
    unresolved = [u for r in records for u in _as_list(r.get("unresolved"))]
    return {
        "layers_grounded": layers,
        "layers_cited": cited,
        "layer_citation_rate": round(cited / layers, 4) if layers else None,
        "ids_claimed": claimed,
        "ids_resolved": resolved,
        # Fabricated or stale ids. A non-zero value here is the signal that the
        # model is inventing identifiers rather than citing what it was given.
        "ids_unresolved": len(unresolved),
        "unresolved_sample": sorted(set(unresolved))[:5],
        "id_resolution_rate": round(resolved / claimed, 4) if claimed else None,
    }


def citable_layers(records: Optional[Iterable[Any]] = None) -> list[str]:
    """Layers that can carry a citation under this contract."""
    return ["layer2_incident_view", "layer3_evidence_pack", "layer4_action_plan"]
