"""AI history: the PURE half (AIH-01/02/03/06). No database, no Mongo, no I/O.

One implementation of everything that must agree byte for byte wherever it is
computed:

* the per-test ``content_sha256`` (Python here, SQL in
  :data:`ANALYSIS_CONTENT_SHA256_SQL`, and a literal copy in migration 0205);
* the run digest (:func:`assemble_digest`) and its hash, deterministic under any
  input order, so regenerating unchanged data never reads as a change;
* the structured diff of two digests (:func:`diff_digests`), used for two
  versions of one run (``mode="versions"``) and for two runs
  (``mode="cross_run"``), with one output shape;
* the derived summaries (:func:`build_ai_changes`, :func:`brief_from_diff`,
  :func:`digest_side`, :func:`meta_from_digest`, :func:`meta_from_summary`);
* the retention rule (:func:`plan_prune`, owner decision D41): never version 1,
  never a version a published decision report cites, oldest first.

Free text (root causes, cluster labels, errors, actions) is LLM output over
CI-supplied strings: it is sanitized (``privacy_service``) and
whitespace-normalised BEFORE it is hashed or cut, and callers render it as
text only. Contract: ``docs/deferred-scope-2026-10-13/work/W03-contract.md``
sections 1.5, 2, 3 and 8.2.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import AbstractSet, Any, Iterable, Mapping, Optional, Sequence

from app.services.flaky_signals import error_signature
from app.services.privacy_service import sanitize_for_persistence

# ── Constants ────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1
KIND_SUMMARY = "run_summary"
KIND_DIGEST = "analysis_digest"
KINDS = (KIND_SUMMARY, KIND_DIGEST)

#: ``ai_analysis_versions.trigger`` vocabulary (a code constant, no CHECK).
TEST_TRIGGERS = ("pipeline", "manual_analyze", "human_correction", "bulk_reclassify", "backfill")
#: ``ai_run_versions.trigger`` vocabulary.
RUN_TRIGGERS = ("pipeline", "backfill")

CONFIDENCE_DELTA_MIN = 10
CLUSTER_JACCARD_MIN = 0.5

MAX_TESTS_IN_DIGEST = 2000
MAX_CLUSTERS = 50
MAX_CLUSTER_MEMBERS = 200
MAX_ACTIONS = 50
EXCERPT_CHARS = 300
ACTION_CHARS = 500
HEADLINE_CHARS = 200
MAX_DIFF_TEST_CHANGES = 200

DEFAULT_CAP_PER_RUN, DEFAULT_CAP_PER_TEST, CAP_MIN, CAP_MAX = 20, 10, 1, 100

UNCATEGORIZED = "UNCATEGORIZED"
_VERDICT_RANK = {"GO": 0, "CONDITIONAL_GO": 1, "NO_GO": 2}
_DETERMINISTIC_MODES = frozenset({"rules", "ml"})
#: provenance keys excluded from the digest hash (they describe the capture,
#: not the content).
_HASH_EXCLUDED_PROVENANCE = ("pipeline_run_id", "source", "captured_at")


@dataclass(frozen=True)
class AIHistoryCaps:
    per_run: int = DEFAULT_CAP_PER_RUN
    per_test: int = DEFAULT_CAP_PER_TEST


@dataclass(frozen=True)
class VersionRef:
    id: str
    version: int
    pipeline_run_id: Optional[str]


# ── Hashing and text ─────────────────────────────────────────────────────────


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise_text(s: Optional[str]) -> str:
    """``" ".join(s.split())``; ``""`` for None. Whitespace never reads as a change."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return " ".join(s.split())


#: How much whitespace-normalised text is redacted before an excerpt is cut.
#: Redaction cost grows with the length of a single token, so an LLM root cause
#: carrying a multi-kilobyte blob must not cost seconds per row; the window is
#: far wider than any excerpt, and a token cut by it is dropped (below), so a
#: partial secret cannot slip past the patterns at the cut.
_REDACTION_WINDOW = 2000


def clean_text(s: Any, limit: Optional[int] = None) -> str:
    """Sanitize (secrets/PII) and whitespace-normalise, then cut: the digest's free-text rule.

    Whitespace never reads as a change and nothing is cut before it is redacted.
    """
    if s is None:
        return ""
    text = normalise_text(s if isinstance(s, str) else str(s))
    if limit is not None and len(text) > _REDACTION_WINDOW:
        window = text[:_REDACTION_WINDOW]
        cut = window.rfind(" ")
        text = window[:cut] if cut > limit else window
    try:
        text = sanitize_for_persistence(text) or ""
    except Exception:  # noqa: BLE001 -- a redaction fault must not leak raw text
        text = ""
    text = normalise_text(text)
    return text[:limit] if limit is not None else text


def _sanitized_first_line(s: Any) -> str:
    """The first line of a raw error, redacted: what a cluster signature reads."""
    if s is None:
        return ""
    text = (s if isinstance(s, str) else str(s)).strip()
    if not text:
        return ""
    first = text.splitlines()[0][:500]
    try:
        return sanitize_for_persistence(first) or ""
    except Exception:  # noqa: BLE001
        return ""


def root_cause_hash(text: Optional[str]) -> Optional[str]:
    """sha256 of the whitespace-normalised root cause, 16 hex; None when empty."""
    norm = normalise_text(text)
    if not norm:
        return None
    return sha256_hex(norm)[:16]


def enum_value(value: Any) -> Any:
    """``FailureCategory.PRODUCT_BUG`` -> ``"PRODUCT_BUG"``; anything else unchanged."""
    if isinstance(value, Enum):
        return value.value
    return value


def _json_scalar_text(value: Any) -> str:
    """What PostgreSQL's ``->>`` yields for a JSON scalar, '' for null."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False)


#: sha256 over the material fields of one ``ai_analysis`` row, joined by U+001F.
#: ``{a}`` is the row alias. Migration 0205 carries a literal copy (a unit test
#: asserts they are equal) and :func:`analysis_content_sha256` is its Python twin.
ANALYSIS_CONTENT_SHA256_SQL = (
    "encode(sha256(convert_to(concat_ws(E'\\x1f', 'v1', "
    "coalesce({a}.failure_category, ''), "
    "coalesce(CAST({a}.confidence_score AS text), ''), "
    "CASE WHEN coalesce({a}.is_flaky, false) THEN '1' ELSE '0' END, "
    "CASE WHEN coalesce({a}.requires_human_review, false) THEN '1' ELSE '0' END, "
    "CASE WHEN coalesce({a}.backend_error_found, false) THEN '1' ELSE '0' END, "
    "CASE WHEN coalesce({a}.pod_issue_found, false) THEN '1' ELSE '0' END, "
    "coalesce({a}.llm_provider, ''), "
    "coalesce({a}.llm_model, ''), "
    "coalesce({a}.routing_metadata->>'analysis_mode', ''), "
    "coalesce({a}.root_cause_summary, '')), 'UTF8')), 'hex')"
)


def analysis_content_sha256(
    *,
    failure_category: Any,
    confidence_score: Optional[int],
    is_flaky: Any,
    requires_human_review: Any,
    backend_error_found: Any,
    pod_issue_found: Any,
    llm_provider: Optional[str],
    llm_model: Optional[str],
    analysis_mode: Any,
    root_cause_summary: Optional[str],
) -> str:
    category = enum_value(failure_category)
    parts = [
        "v1",
        str(category) if category is not None else "",
        str(int(confidence_score)) if confidence_score is not None else "",
        "1" if is_flaky else "0",
        "1" if requires_human_review else "0",
        "1" if backend_error_found else "0",
        "1" if pod_issue_found else "0",
        llm_provider or "",
        llm_model or "",
        _json_scalar_text(analysis_mode),
        root_cause_summary or "",
    ]
    return sha256_hex("\x1f".join(parts))


def _without_capture_fields(digest: Mapping[str, Any]) -> dict:
    body = {k: v for k, v in digest.items() if k != "content_sha256"}
    prov = body.get("provenance")
    if isinstance(prov, Mapping):
        body["provenance"] = {
            k: v for k, v in prov.items() if k not in _HASH_EXCLUDED_PROVENANCE
        }
    return body


def digest_content_sha256(digest: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(_without_capture_fields(digest)))


_SUMMARY_HASH_KEYS = (
    "layer1_executive_summary",
    "layer2_incident_view",
    "layer3_evidence_pack",
    "layer4_action_plan",
    "executive_panel",
    "fallback_used",
)


def summary_content_sha256(payload: Mapping[str, Any]) -> str:
    view = {key: payload.get(key) for key in _SUMMARY_HASH_KEYS}
    view["fallback_used"] = bool(view.get("fallback_used"))
    return sha256_hex(canonical_json(view))


# ── Digest building blocks ───────────────────────────────────────────────────


def cluster_signature(representative_error: Optional[str], label: Optional[str]) -> str:
    """Error-kind signature of a cluster: its representative error, else its label."""
    return error_signature(representative_error) or error_signature(label) or "unlabelled"


def derive_engine(mode_counts: Mapping[str, int]) -> Optional[str]:
    modes = sorted(k for k, n in (mode_counts or {}).items() if k and n)
    if not modes:
        return None
    if len(modes) == 1:
        return modes[0]
    return "mixed"


def _most_common(values: Iterable[Any]) -> Optional[str]:
    counts = Counter(
        v for v in values if isinstance(v, str) and v.strip() and v.strip().lower() != "none"
    )
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _round1(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> Optional[str]:
    value = enum_value(value)
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _merge_prompt_versions(rows: Iterable[Mapping[str, Any]]) -> dict:
    per_key: dict[str, Counter] = {}
    for meta in rows:
        versions = meta.get("prompt_versions") if isinstance(meta, Mapping) else None
        if not isinstance(versions, Mapping):
            continue
        for key, value in versions.items():
            if value is None:
                continue
            per_key.setdefault(str(key), Counter())[str(value)] += 1
    return {
        key: sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for key, counts in sorted(per_key.items())
    }


def assemble_digest(
    *,
    run: Mapping[str, Any],
    analyses: Sequence[Mapping[str, Any]],
    clusters: Sequence[Mapping[str, Any]] = (),
    fingerprint_by_test_id: Optional[Mapping[str, str]] = None,
    verdict: Optional[Mapping[str, Any]] = None,
    actions: Sequence[Any] = (),
    actions_source: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    source: str = "live",
    captured_at: Optional[str] = None,
    stage_quality: Optional[str] = None,
    summary_fallback_used: Optional[bool] = None,
    carried_over_tests: Optional[int] = None,
    partial: Sequence[str] = (),
) -> dict:
    """Build the §2 digest from already-loaded rows. Pure and order-independent.

    ``analyses``: one mapping per ``ai_analysis`` row of the run, carrying the
    test case's ``test_case_id``, ``test_fingerprint``, ``test_name``,
    ``suite_name``, ``status`` and the analysis' ``failure_category``,
    ``confidence_score``, ``root_cause_summary``, ``is_flaky``,
    ``requires_human_review``, ``llm_provider``, ``llm_model``,
    ``routing_metadata``. ``clusters``: ``failure_clusters`` rows
    (``cluster_id``, ``label``, ``representative_error``, ``size``,
    ``member_test_ids``). ``fingerprint_by_test_id`` maps THIS run's test case
    ids to fingerprints; a member id missing from it (another run's test) is
    dropped, so it can never leak into the digest.
    """
    fingerprint_by_test_id = {str(k): v for k, v in (fingerprint_by_test_id or {}).items()}
    verdict = dict(verdict or {})

    # ── tests (all analysed rows; the list is capped later) ────────────────
    tests: list[dict] = []
    categories: Counter = Counter()
    confidences: list[int] = []
    flaky_count = 0
    review_count = 0
    mode_counts: Counter = Counter()
    fallback_count = 0
    routing_rows: list[Mapping[str, Any]] = []
    providers: list[Any] = []
    models: list[Any] = []
    for row in analyses:
        routing = row.get("routing_metadata")
        routing = routing if isinstance(routing, Mapping) else {}
        routing_rows.append(routing)
        category = _str_or_none(row.get("failure_category"))
        confidence = _int_or_none(row.get("confidence_score"))
        mode = routing.get("analysis_mode")
        mode = mode if isinstance(mode, str) and mode else None
        is_flaky = bool(row.get("is_flaky"))
        review = bool(row.get("requires_human_review"))
        categories[category or UNCATEGORIZED] += 1
        if confidence is not None:
            confidences.append(confidence)
        flaky_count += 1 if is_flaky else 0
        review_count += 1 if review else 0
        if mode:
            mode_counts[mode] += 1
        if routing.get("fallback_from"):
            fallback_count += 1
        providers.append(row.get("llm_provider"))
        models.append(row.get("llm_model"))
        root_cause = row.get("root_cause_summary")
        tests.append({
            "test_fingerprint": str(row.get("test_fingerprint") or ""),
            "test_id": str(row.get("test_case_id") or ""),
            "test_name": row.get("test_name"),
            "suite_name": row.get("suite_name"),
            "status": _str_or_none(row.get("status")),
            "category": category,
            "confidence": confidence,
            "root_cause_sha": root_cause_hash(root_cause),
            "root_cause_excerpt": clean_text(root_cause, EXCERPT_CHARS),
            "is_flaky": is_flaky,
            "requires_human_review": review,
            "analysis_mode": mode,
        })
    tests.sort(key=lambda t: (t["test_fingerprint"], t["test_id"]))
    tests_total = len(tests)
    tests_truncated = tests_total > MAX_TESTS_IN_DIGEST
    tests = tests[:MAX_TESTS_IN_DIGEST]

    # ── clusters ───────────────────────────────────────────────────────────
    built: list[dict] = []
    for cluster in clusters:
        label = clean_text(cluster.get("label"), 500)
        rep_raw = cluster.get("representative_error")
        rep = clean_text(rep_raw, EXCERPT_CHARS) or None
        members_raw = cluster.get("member_test_ids") or []
        if not isinstance(members_raw, (list, tuple)):
            members_raw = []
        fingerprints = sorted({
            fingerprint_by_test_id[str(m)]
            for m in members_raw
            if str(m) in fingerprint_by_test_id and fingerprint_by_test_id[str(m)]
        })
        built.append({
            "base_signature": cluster_signature(_sanitized_first_line(rep_raw), label),
            "cluster_id": str(cluster.get("cluster_id") or ""),
            "label": label,
            "size": _int_or_none(cluster.get("size")) or 0,
            "member_count": len(fingerprints),
            "member_fingerprints": fingerprints[:MAX_CLUSTER_MEMBERS],
            "representative_error": rep,
        })
    # Same signature twice -> "#2", "#3" by (-size, cluster_id).
    built.sort(key=lambda c: (c["base_signature"], -c["size"], c["cluster_id"], c["label"]))
    seen: Counter = Counter()
    out_clusters: list[dict] = []
    for c in built:
        base = c.pop("base_signature")
        seen[base] += 1
        c["signature"] = base if seen[base] == 1 else f"{base} #{seen[base]}"
        out_clusters.append(c)
    out_clusters.sort(key=lambda c: (-c["size"], c["signature"]))
    clusters_total = len(out_clusters)
    clusters_truncated = clusters_total > MAX_CLUSTERS
    out_clusters = out_clusters[:MAX_CLUSTERS]
    out_clusters = [
        {
            "signature": c["signature"],
            "cluster_id": c["cluster_id"],
            "label": c["label"],
            "size": c["size"],
            "member_count": c["member_count"],
            "member_fingerprints": c["member_fingerprints"],
            "representative_error": c["representative_error"],
        }
        for c in out_clusters
    ]

    # ── verdict + actions ──────────────────────────────────────────────────
    out_verdict = {
        "recommendation": _str_or_none(verdict.get("recommendation")),
        "risk_score": _int_or_none(verdict.get("risk_score")),
        "composite_risk": _round1(verdict.get("composite_risk")),
        "source": verdict.get("source"),
        "decision_report_version": _int_or_none(verdict.get("decision_report_version")),
        "overridden": bool(verdict.get("overridden")),
    }
    cleaned_actions = sorted({
        text for text in (clean_text(a, ACTION_CHARS) for a in (actions or [])) if text
    })[:MAX_ACTIONS]

    # ── provenance ─────────────────────────────────────────────────────────
    engine = derive_engine(mode_counts)
    deterministic = bool(mode_counts) and all(m in _DETERMINISTIC_MODES for m in mode_counts)
    provenance = {
        "pipeline_run_id": str(pipeline_run_id) if pipeline_run_id else None,
        "source": source,
        "captured_at": captured_at,
        "engine": engine,
        "mode_counts": {k: mode_counts[k] for k in sorted(mode_counts)},
        "llm_provider": _most_common(providers),
        "llm_model": _most_common(models),
        "fallback_used": fallback_count > 0,
        "fallback_count": fallback_count,
        "deterministic": deterministic,
        "summary_fallback_used": summary_fallback_used,
        "carried_over_tests": carried_over_tests,
        "stage_quality": stage_quality,
        "prompt_versions": _merge_prompt_versions(routing_rows),
        "partial": sorted(set(partial or ())),
    }

    digest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_DIGEST,
        "run": {
            "run_id": str(run.get("run_id") or run.get("id") or ""),
            "project_id": str(run.get("project_id") or ""),
            "build_number": _str_or_none(run.get("build_number")),
            "branch": _str_or_none(run.get("branch")),
            "primary_suite_name": _str_or_none(run.get("primary_suite_name")),
            "total_tests": _int_or_none(run.get("total_tests")) or 0,
            "failed_tests": _int_or_none(run.get("failed_tests")) or 0,
            "broken_tests": _int_or_none(run.get("broken_tests")) or 0,
            "pass_rate": _round1(run.get("pass_rate")),
        },
        "verdict": out_verdict,
        "analysed_tests": tests_total,
        "categories": {k: categories[k] for k in sorted(categories) if categories[k] > 0},
        "avg_confidence": (
            round(sum(confidences) / len(confidences), 1) if confidences else None
        ),
        "flaky_count": flaky_count,
        "requires_review_count": review_count,
        "clusters": out_clusters,
        "tests": tests,
        "actions": cleaned_actions,
        "actions_source": actions_source,
        "provenance": provenance,
        "limits": {
            "tests_total": tests_total,
            "tests_truncated": tests_truncated,
            "clusters_total": clusters_total,
            "clusters_truncated": clusters_truncated,
        },
    }
    digest["content_sha256"] = digest_content_sha256(digest)
    return digest


# ── Projections ──────────────────────────────────────────────────────────────


def meta_from_digest(digest: Mapping[str, Any]) -> dict:
    """The ``meta`` block of an ``analysis_digest`` doc (§1.3): the list view reads only this."""
    verdict = digest.get("verdict") or {}
    clusters = digest.get("clusters")
    categories = digest.get("categories")
    return {
        "verdict": verdict.get("recommendation"),
        "risk_score": _int_or_none(verdict.get("risk_score")),
        "analysed_tests": _int_or_none(digest.get("analysed_tests")),
        "cluster_count": len(clusters) if isinstance(clusters, list) else None,
        "categories": dict(categories) if isinstance(categories, Mapping) else None,
        "headline": None,
        "decision_report_ref": None,
        "summary_version": None,
    }


def meta_from_summary(payload: Mapping[str, Any]) -> dict:
    headline = normalise_text(payload.get("executive_summary"))[:HEADLINE_CHARS]
    return {
        "verdict": None,
        "risk_score": None,
        "analysed_tests": _int_or_none(payload.get("analysis_count")),
        "cluster_count": None,
        "categories": None,
        "headline": headline or None,
        "decision_report_ref": None,
        "summary_version": None,
    }


def provenance_from_digest(digest: Mapping[str, Any]) -> dict:
    """The doc-level ``provenance`` block of an ``analysis_digest`` version."""
    prov = digest.get("provenance") or {}
    return {
        "analysis_mode": prov.get("engine"),
        "llm_provider": prov.get("llm_provider"),
        "llm_model": prov.get("llm_model"),
        "fallback_used": bool(prov.get("fallback_used")),
        "deterministic": bool(prov.get("deterministic")),
        "prompt_versions": dict(prov.get("prompt_versions") or {}),
        "source": "stored",
    }


def provenance_from_summary(payload: Mapping[str, Any]) -> dict:
    """The doc-level ``provenance`` block of a ``run_summary`` version."""
    summary_prov = payload.get("summary_provenance")
    summary_prov = summary_prov if isinstance(summary_prov, Mapping) else {}
    snapshot = summary_prov.get("model_config_snapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    fallback_used = bool(payload.get("fallback_used"))
    versions = summary_prov.get("prompt_versions")
    return {
        "analysis_mode": "deterministic" if fallback_used else "llm",
        "llm_provider": snapshot.get("provider"),
        "llm_model": snapshot.get("model"),
        "fallback_used": fallback_used,
        "deterministic": fallback_used,
        "prompt_versions": dict(versions) if isinstance(versions, Mapping) else {},
        "source": "stored",
    }


def digest_side(digest: Mapping[str, Any]) -> dict:
    """Compact per-side summary for the Run Compare columns (§6.2 DigestSide)."""
    verdict = digest.get("verdict") or {}
    prov = digest.get("provenance") or {}
    clusters = [c for c in (digest.get("clusters") or []) if isinstance(c, Mapping)]
    top = sorted(clusters, key=lambda c: (-(c.get("size") or 0), str(c.get("signature") or "")))[:5]
    return {
        "verdict": verdict.get("recommendation"),
        "risk_score": _int_or_none(verdict.get("risk_score")),
        "composite_risk": _round1(verdict.get("composite_risk")),
        "analysed_tests": _int_or_none(digest.get("analysed_tests")) or 0,
        "categories": dict(digest.get("categories") or {}),
        "avg_confidence": _round1(digest.get("avg_confidence")),
        "top_clusters": [
            {"label": c.get("label"), "size": c.get("size"), "signature": c.get("signature")}
            for c in top
        ],
        "engine": prov.get("engine"),
        "llm_provider": prov.get("llm_provider"),
        "llm_model": prov.get("llm_model"),
        "deterministic": bool(prov.get("deterministic")),
        "fallback_used": bool(prov.get("fallback_used")),
        "decision_report_version": _int_or_none(verdict.get("decision_report_version")),
        "generated_at": prov.get("captured_at") if prov.get("source") == "stored" else None,
    }


def is_not_analysed(digest: Mapping[str, Any]) -> bool:
    """No analysed tests, no clusters and no verdict: nothing to compare (§6.7)."""
    verdict = digest.get("verdict") or {}
    return (
        not (_int_or_none(digest.get("analysed_tests")) or 0)
        and not (digest.get("clusters") or [])
        and verdict.get("recommendation") is None
    )


# ── Diff ─────────────────────────────────────────────────────────────────────


def verdict_direction(frm: Optional[str], to: Optional[str]) -> str:
    if frm is None or to is None or frm not in _VERDICT_RANK or to not in _VERDICT_RANK:
        return "unknown"
    a, b = _VERDICT_RANK[frm], _VERDICT_RANK[to]
    if b > a:
        return "worse"
    if b < a:
        return "better"
    return "same"


def _pair(frm: Any, to: Any) -> Optional[dict]:
    return None if frm == to else {"from": frm, "to": to}


def _engine_word(value: Any) -> str:
    return str(value) if value else "none"


def _diff_provenance(a: Mapping[str, Any], b: Mapping[str, Any], mode: str) -> dict:
    engine = _pair(a.get("engine"), b.get("engine"))
    model = None
    if (a.get("llm_provider"), a.get("llm_model")) != (b.get("llm_provider"), b.get("llm_model")):
        model = {
            "from": a.get("llm_model"),
            "to": b.get("llm_model"),
            "provider_from": a.get("llm_provider"),
            "provider_to": b.get("llm_provider"),
        }
    fallback = _pair(bool(a.get("fallback_used")), bool(b.get("fallback_used")))
    deterministic = _pair(bool(a.get("deterministic")), bool(b.get("deterministic")))
    mode_counts = _pair(dict(a.get("mode_counts") or {}), dict(b.get("mode_counts") or {}))
    changed = any(x is not None for x in (engine, model, fallback, deterministic, mode_counts))
    note = None
    if engine is not None:
        frm, to = _engine_word(engine["from"]), _engine_word(engine["to"])
        if mode == "cross_run":
            note = (
                f"Baseline analysed by {frm}; current by {to}. "
                "Category changes may reflect the engine, not the product."
            )
        else:
            note = f"The analysis engine changed between these versions ({frm} -> {to})."
    elif model is not None:
        frm = _engine_word(model["from"] or model["provider_from"])
        to = _engine_word(model["to"] or model["provider_to"])
        if mode == "cross_run":
            note = (
                f"Baseline analysed with {frm}; current with {to}. "
                "Category changes may reflect the model, not the product."
            )
        else:
            note = f"The analysis model changed between these versions ({frm} -> {to})."
    return {
        "changed": changed,
        "engine": engine,
        "model": model,
        "fallback_used": fallback,
        "deterministic": deterministic,
        "mode_counts": mode_counts,
        "engine_only": False,  # filled once the material sections are known
        "note": note,
    }


def _diff_verdict(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict:
    frm, to = a.get("recommendation"), b.get("recommendation")
    fr, tr = _int_or_none(a.get("risk_score")), _int_or_none(b.get("risk_score"))
    return {
        "changed": frm != to,
        "from": frm,
        "to": to,
        "direction": verdict_direction(frm, to),
        "from_risk": fr,
        "to_risk": tr,
        "risk_delta": (tr - fr) if fr is not None and tr is not None else None,
    }


def _diff_categories(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict:
    a_cat = dict(a.get("categories") or {})
    b_cat = dict(b.get("categories") or {})
    changed = []
    for key in set(a_cat) | set(b_cat):
        frm, to = int(a_cat.get(key) or 0), int(b_cat.get(key) or 0)
        if frm != to:
            changed.append({"category": key, "from": frm, "to": to, "delta": to - frm})
    changed.sort(key=lambda c: (-abs(c["delta"]), c["category"]))

    def _total(d: Mapping[str, Any], cats: Mapping[str, Any]) -> int:
        value = _int_or_none(d.get("analysed_tests"))
        return value if value is not None else sum(int(v or 0) for v in cats.values())

    return {"changed": changed, "from_total": _total(a, a_cat), "to_total": _total(b, b_cat)}


def _jaccard(x: AbstractSet[str], y: AbstractSet[str]) -> Optional[float]:
    if not x or not y:
        return None
    return round(len(x & y) / len(x | y), 2)


def _cluster_ref(c: Mapping[str, Any]) -> dict:
    return {
        "signature": c.get("signature"),
        "label": c.get("label"),
        "size": _int_or_none(c.get("size")) or 0,
        "member_count": _int_or_none(c.get("member_count")) or 0,
    }


def _diff_clusters(a_list: Sequence[Mapping[str, Any]], b_list: Sequence[Mapping[str, Any]]) -> dict:
    a_list = [c for c in a_list if isinstance(c, Mapping)]
    b_list = [c for c in b_list if isinstance(c, Mapping)]
    unmatched_a = list(range(len(a_list)))
    unmatched_b = list(range(len(b_list)))
    pairs: list[tuple[int, int, str]] = []

    # Step 1: exact signature, one-to-one.
    by_sig_b: dict[str, list[int]] = {}
    for j in unmatched_b:
        by_sig_b.setdefault(str(b_list[j].get("signature") or ""), []).append(j)
    for i in sorted(unmatched_a, key=lambda i: str(a_list[i].get("signature") or "")):
        sig = str(a_list[i].get("signature") or "")
        if not sig or not by_sig_b.get(sig):
            continue
        j = by_sig_b[sig].pop(0)
        pairs.append((i, j, "signature"))
    matched_a = {i for i, _, _ in pairs}
    matched_b = {j for _, j, _ in pairs}
    unmatched_a = [i for i in unmatched_a if i not in matched_a]
    unmatched_b = [j for j in unmatched_b if j not in matched_b]

    # Step 2: member overlap, greedy by (jaccard desc, signature_from, signature_to).
    members_a = {i: set(a_list[i].get("member_fingerprints") or []) for i in unmatched_a}
    members_b = {j: set(b_list[j].get("member_fingerprints") or []) for j in unmatched_b}
    candidates = []
    for i in unmatched_a:
        for j in unmatched_b:
            score = _jaccard(members_a[i], members_b[j])
            if score is not None and score >= CLUSTER_JACCARD_MIN:
                candidates.append((
                    -score,
                    str(a_list[i].get("signature") or ""),
                    str(b_list[j].get("signature") or ""),
                    i,
                    j,
                ))
    candidates.sort()
    used_a: set[int] = set()
    used_b: set[int] = set()
    for _neg, _sa, _sb, i, j in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j, "overlap"))

    matched = []
    for i, j, how in pairs:
        ca, cb = a_list[i], b_list[j]
        size_from = _int_or_none(ca.get("size")) or 0
        size_to = _int_or_none(cb.get("size")) or 0
        matched.append({
            "signature_from": ca.get("signature"),
            "signature_to": cb.get("signature"),
            "label_from": ca.get("label"),
            "label_to": cb.get("label"),
            "size_from": size_from,
            "size_to": size_to,
            "delta": size_to - size_from,
            "matched_by": how,
            "jaccard": _jaccard(
                set(ca.get("member_fingerprints") or []), set(cb.get("member_fingerprints") or [])
            ),
            "resized": size_from != size_to,
        })
    matched.sort(key=lambda m: (-abs(m["delta"]), str(m["signature_to"] or ""), str(m["signature_from"] or "")))
    done_a = {i for i, _, _ in pairs}
    done_b = {j for _, j, _ in pairs}
    added = sorted(
        (_cluster_ref(b_list[j]) for j in range(len(b_list)) if j not in done_b),
        key=lambda c: (-c["size"], str(c["signature"] or "")),
    )
    removed = sorted(
        (_cluster_ref(a_list[i]) for i in range(len(a_list)) if i not in done_a),
        key=lambda c: (-c["size"], str(c["signature"] or "")),
    )
    return {
        "added": added,
        "removed": removed,
        "matched": matched,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "matched": len(matched),
            "resized": sum(1 for m in matched if m["resized"]),
        },
    }


def _test_ref(t: Mapping[str, Any]) -> dict:
    return {
        "test_fingerprint": t.get("test_fingerprint"),
        "test_id": t.get("test_id"),
        "test_name": t.get("test_name"),
        "suite_name": t.get("suite_name"),
        "category": t.get("category"),
        "confidence": _int_or_none(t.get("confidence")),
    }


def _ref_sort_key(t: Mapping[str, Any]) -> tuple:
    return (
        t.get("category") or "",
        -(t.get("confidence") or 0),
        t.get("test_name") or "",
        t.get("test_fingerprint") or "",
    )


def _diff_tests(a_list: Sequence[Mapping[str, Any]], b_list: Sequence[Mapping[str, Any]]) -> dict:
    a_by = {str(t.get("test_fingerprint")): t for t in a_list if isinstance(t, Mapping)}
    b_by = {str(t.get("test_fingerprint")): t for t in b_list if isinstance(t, Mapping)}
    added = sorted((_test_ref(b_by[fp]) for fp in b_by if fp not in a_by), key=_ref_sort_key)
    removed = sorted((_test_ref(a_by[fp]) for fp in a_by if fp not in b_by), key=_ref_sort_key)
    changed = []
    unchanged = 0
    for fp in sorted(set(a_by) & set(b_by)):
        ta, tb = a_by[fp], b_by[fp]
        kinds = []
        cat_from, cat_to = ta.get("category"), tb.get("category")
        category = None
        if cat_from != cat_to:
            kinds.append("category")
            category = {"from": cat_from, "to": cat_to}
        conf_from, conf_to = _int_or_none(ta.get("confidence")), _int_or_none(tb.get("confidence"))
        confidence = None
        if conf_from is not None and conf_to is not None and abs(conf_to - conf_from) >= CONFIDENCE_DELTA_MIN:
            kinds.append("confidence")
            confidence = {"from": conf_from, "to": conf_to, "delta": conf_to - conf_from}
        flaky = None
        if bool(ta.get("is_flaky")) != bool(tb.get("is_flaky")):
            kinds.append("flaky")
            flaky = {"from": bool(ta.get("is_flaky")), "to": bool(tb.get("is_flaky"))}
        root_cause_changed = ta.get("root_cause_sha") != tb.get("root_cause_sha")
        if root_cause_changed:
            kinds.append("root_cause")
        if not kinds:
            unchanged += 1
            continue
        material = bool(category or confidence or flaky)
        changed.append({
            "test_fingerprint": fp,
            "test_id": tb.get("test_id"),
            "from_test_id": ta.get("test_id"),
            "test_name": tb.get("test_name"),
            "suite_name": tb.get("suite_name"),
            "kinds": kinds,
            "material": material,
            "category": category,
            "confidence": confidence,
            "flaky": flaky,
            "root_cause_changed": root_cause_changed,
            "root_cause_excerpt": (
                {"from": ta.get("root_cause_excerpt") or "", "to": tb.get("root_cause_excerpt") or ""}
                if root_cause_changed else None
            ),
            # Both sides' values, so a brief or a bullet can name a test's
            # category and confidence even when only one of them moved
            # (additive fields beyond the pinned shape).
            "previous_category": cat_from,
            "current_category": cat_to,
            "previous_confidence": conf_from,
            "current_confidence": conf_to,
        })
    changed.sort(key=lambda c: (
        not c["material"],
        -abs((c["confidence"] or {}).get("delta") or 0),
        c["test_name"] or "",
        c["test_fingerprint"],
    ))
    counts = {
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "material_changed": sum(1 for c in changed if c["material"]),
    }
    truncated = any(len(x) > MAX_DIFF_TEST_CHANGES for x in (added, removed, changed))
    return {
        "added": added[:MAX_DIFF_TEST_CHANGES],
        "removed": removed[:MAX_DIFF_TEST_CHANGES],
        "changed": changed[:MAX_DIFF_TEST_CHANGES],
        "persisting_unchanged": unchanged,
        "counts": counts,
        "truncated": truncated,
    }


def diff_digests(a: Optional[Mapping[str, Any]], b: Optional[Mapping[str, Any]], *, mode: str) -> dict:
    """Structured diff of two digests: ``a`` is the older side ("from"), ``b`` the newer.

    ``mode`` is ``"versions"`` (two digests of one run) or ``"cross_run"``. The
    shape is identical in both; only the UI's labels differ. Provenance is
    computed first and rendered first: an engine change (rules -> LLM) can
    explain every category change below it. Tolerant of an older or partial
    stored shape (a missing section reads as empty), so a read never 500s.
    """
    a = a if isinstance(a, Mapping) else {}
    b = b if isinstance(b, Mapping) else {}
    if mode not in ("versions", "cross_run"):
        raise ValueError(f"unknown diff mode {mode!r}")
    provenance = _diff_provenance(a.get("provenance") or {}, b.get("provenance") or {}, mode)
    verdict = _diff_verdict(a.get("verdict") or {}, b.get("verdict") or {})
    categories = _diff_categories(a, b)
    clusters = _diff_clusters(a.get("clusters") or [], b.get("clusters") or [])
    tests = _diff_tests(a.get("tests") or [], b.get("tests") or [])
    a_actions = {str(x) for x in (a.get("actions") or [])}
    b_actions = {str(x) for x in (b.get("actions") or [])}
    actions = {"added": sorted(b_actions - a_actions), "removed": sorted(a_actions - b_actions)}

    material = (
        verdict["changed"]
        or bool(categories["changed"])
        or clusters["counts"]["added"]
        or clusters["counts"]["removed"]
        or clusters["counts"]["resized"]
        or tests["counts"]["added"]
        or tests["counts"]["removed"]
        or tests["counts"]["material_changed"]
        or actions["added"]
        or actions["removed"]
    )
    provenance["engine_only"] = bool(provenance["changed"] and not material)
    has_changes = bool(
        provenance["changed"]
        or material
        or tests["counts"]["changed"]
    )
    a_sha, b_sha = a.get("content_sha256"), b.get("content_sha256")
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "identical": bool(a_sha and b_sha and a_sha == b_sha),
        "has_changes": has_changes,
        "provenance": provenance,
        "verdict": verdict,
        "categories": categories,
        "clusters": clusters,
        "tests": tests,
        "actions": actions,
    }


# ── Derived summaries ────────────────────────────────────────────────────────


def _ascii(value: Any) -> str:
    text = normalise_text("" if value is None else str(value))
    return text.encode("ascii", "replace").decode("ascii")


def _num(value: Any) -> str:
    return "n/a" if value is None else str(value)


def build_ai_changes(diff: Mapping[str, Any], *, limit: int = 12) -> list[str]:
    """Plain ASCII bullet lines, most important first (§3.3). Deterministic."""
    lines: list[str] = []
    prov = diff.get("provenance") or {}
    if prov.get("note"):
        lines.append(_ascii(prov["note"]))
    verdict = diff.get("verdict") or {}
    fr, tr = verdict.get("from_risk"), verdict.get("to_risk")
    if verdict.get("changed"):
        line = f"Verdict {_ascii(verdict.get('from') or 'none')} -> {_ascii(verdict.get('to') or 'none')}"
        if fr is not None and tr is not None:
            line += f" (risk {fr} -> {tr})"
        lines.append(line)
    elif verdict.get("risk_delta") is not None and abs(verdict["risk_delta"]) >= 10:
        lines.append(f"Risk {fr} -> {tr}")
    clusters = diff.get("clusters") or {}
    for c in (clusters.get("added") or [])[:3]:
        lines.append(f"New root cause: {_ascii(c.get('label'))} ({c.get('size') or 0} tests)")
    for c in (clusters.get("removed") or [])[:3]:
        lines.append(f"Resolved: {_ascii(c.get('label'))} ({c.get('size') or 0} tests)")
    category_changes = [
        t for t in ((diff.get("tests") or {}).get("changed") or [])
        if t.get("material") and t.get("category")
    ]
    for t in category_changes[:5]:
        cat = t["category"]
        lines.append(
            f"{_ascii(t.get('test_name') or t.get('test_fingerprint'))}: "
            f"{_ascii(cat.get('from') or 'none')} -> {_ascii(cat.get('to') or 'none')} "
            f"(confidence {_num(t.get('previous_confidence'))} -> {_num(t.get('current_confidence'))})"
        )
    limit = max(1, int(limit))
    if len(lines) > limit:
        kept = lines[: limit - 1]
        kept.append(f"+{len(lines) - len(kept)} more changes")
        return kept
    return lines


def brief_from_diff(diff: Mapping[str, Any]) -> dict:
    """``AIAnalysisDiffBrief`` for the run-compare payload (§7.1)."""
    prov = diff.get("provenance") or {}
    clusters = diff.get("clusters") or {}
    tests = diff.get("tests") or {}
    t_counts = tests.get("counts") or {}
    c_counts = clusters.get("counts") or {}
    test_changes = []
    for t in tests.get("changed") or []:
        if not t.get("material"):
            continue
        cat = t.get("category") or {}
        test_changes.append({
            "test_name": t.get("test_name"),
            "suite_name": t.get("suite_name"),
            "category_from": cat.get("from") if cat else t.get("previous_category"),
            "category_to": cat.get("to") if cat else t.get("current_category"),
            "confidence_from": t.get("previous_confidence"),
            "confidence_to": t.get("current_confidence"),
        })
        if len(test_changes) >= 50:
            break
    return {
        "available": True,
        "reason": None,
        "left_ref": "live",
        "right_ref": "live",
        "verdict": dict(diff.get("verdict") or {}),
        "provenance": {
            "changed": bool(prov.get("changed")),
            "engine_only": bool(prov.get("engine_only")),
            "note": prov.get("note"),
            "engine": prov.get("engine"),
        },
        "counts": {
            "clusters_added": int(c_counts.get("added") or 0),
            "clusters_removed": int(c_counts.get("removed") or 0),
            "clusters_resized": int(c_counts.get("resized") or 0),
            "tests_added": int(t_counts.get("added") or 0),
            "tests_removed": int(t_counts.get("removed") or 0),
            "tests_changed": int(t_counts.get("changed") or 0),
            "tests_material_changed": int(t_counts.get("material_changed") or 0),
            "categories_changed": len((diff.get("categories") or {}).get("changed") or []),
        },
        "clusters_added": [
            {"label": c.get("label"), "size": c.get("size")} for c in (clusters.get("added") or [])[:10]
        ],
        "clusters_removed": [
            {"label": c.get("label"), "size": c.get("size")} for c in (clusters.get("removed") or [])[:10]
        ],
        "test_changes": test_changes,
        "ai_changes": build_ai_changes(diff, limit=12),
    }


# ── Retention ────────────────────────────────────────────────────────────────


def clamp_cap(value: Any, default: int) -> int:
    """A stored cap, or the default when NULL / out of range."""
    cap = _int_or_none(value)
    if cap is None or cap < CAP_MIN or cap > CAP_MAX:
        return default
    return cap


def plan_prune(
    versions: Sequence[VersionRef],
    *,
    cap: int,
    cited_pipeline_ids: AbstractSet[str],
) -> list[str]:
    """Ids to delete so at most ``cap`` versions remain (D41), oldest first.

    Protected, whatever the cap: the lowest version (v1, the first answer),
    every version whose pipeline a PUBLISHED decision report cites, and the
    highest version (the current answer: without it a cap of 1 would delete
    each new version the moment it is written and "latest" would read v1). If
    the protected versions alone reach the cap, nothing more is deleted.
    """
    if not versions:
        return []
    cap = max(CAP_MIN, int(cap))
    cited = {str(x) for x in (cited_pipeline_ids or set())}
    ordered = sorted(versions, key=lambda v: (v.version, v.id))
    lowest, highest = ordered[0].version, ordered[-1].version
    candidates = [
        v for v in ordered
        if v.version not in (lowest, highest)
        and not (v.pipeline_run_id and str(v.pipeline_run_id) in cited)
    ]
    excess = max(0, len(ordered) - cap)
    return [v.id for v in candidates[:excess]]
