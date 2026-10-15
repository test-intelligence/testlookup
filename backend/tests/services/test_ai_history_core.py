"""AIH-01/02/03/06: the pure AI-history core (``app/services/ai_history_core.py``).

Every threshold and ordering rule of W03 contract section 3.2 has a table test
here; the digest's determinism (section 2.4) is proven by shuffling its inputs;
``plan_prune`` (D41) by the contract's table; three goldens pin the digest and
both diff modes byte for byte (regenerate with ``UPDATE_AI_HISTORY_GOLDENS=1``
after an intended change, then READ the diff before committing).
"""
from __future__ import annotations

import copy
import json
import os
import random
from pathlib import Path

import pytest

from app.models.ai_history_schemas import AIAnalysisDiffBrief, AIDigest, DigestSide
from app.services import ai_history_core as core
from app.services.ai_history_core import VersionRef

GOLDENS = Path(__file__).resolve().parents[1] / "fixtures" / "ai_history"
UPDATE = os.getenv("UPDATE_AI_HISTORY_GOLDENS") == "1"
MIGRATION_0205 = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0205_ai_analysis_versions.py"

RUN = {
    "run_id": "11111111-1111-4111-8111-111111111111",
    "project_id": "22222222-2222-4222-8222-222222222222",
    "build_number": "1234",
    "branch": "main",
    "primary_suite_name": "checkout",
    "total_tests": 120,
    "failed_tests": 7,
    "broken_tests": 1,
    "pass_rate": 93.333,
}


def _row(fp: str, *, tid: str | None = None, name: str | None = None, cat="PRODUCT_BUG", conf=80,
         cause="Gateway timed out", flaky=False, review=False, mode="llm", provider="ollama",
         model="llama3.1", fallback_from=None, suite="checkout", status="FAILED", prompt=None) -> dict:
    routing = {"analysis_mode": mode, "fallback_from": fallback_from}
    if prompt:
        routing["prompt_versions"] = prompt
    return {
        "test_case_id": tid or f"tc-{fp}",
        "test_fingerprint": fp,
        "test_name": name or f"test_{fp}",
        "suite_name": suite,
        "status": status,
        "failure_category": cat,
        "confidence_score": conf,
        "root_cause_summary": cause,
        "is_flaky": flaky,
        "requires_human_review": review,
        "llm_provider": provider,
        "llm_model": model,
        "routing_metadata": routing,
    }


def _cluster(cid: str, label: str, members: list[str], *, size: int | None = None, error: str | None = None) -> dict:
    return {
        "cluster_id": cid,
        "label": label,
        "representative_error": error,
        "size": size if size is not None else len(members),
        "member_test_ids": members,
    }


def _digest(analyses=None, clusters=(), fps=None, **kw) -> dict:
    analyses = analyses if analyses is not None else [_row("fp1"), _row("fp2", cat="INFRASTRUCTURE", conf=40)]
    fps = fps if fps is not None else {r["test_case_id"]: r["test_fingerprint"] for r in analyses}
    return core.assemble_digest(run=RUN, analyses=analyses, clusters=list(clusters), fingerprint_by_test_id=fps, **kw)


def _ref(fp, cat="PRODUCT_BUG", conf=80, cause="Gateway timed out", flaky=False, name=None) -> dict:
    """A digest test entry, for diff tables."""
    return {
        "test_fingerprint": fp, "test_id": f"id-{fp}", "test_name": name or f"test_{fp}", "suite_name": "s",
        "status": "FAILED", "category": cat, "confidence": conf,
        "root_cause_sha": core.root_cause_hash(cause), "root_cause_excerpt": cause,
        "is_flaky": flaky, "requires_human_review": False, "analysis_mode": "llm",
    }


def _side(tests=(), clusters=(), verdict=None, risk=None, engine="llm", model="llama3.1", actions=(), **prov) -> dict:
    d = {
        "verdict": {"recommendation": verdict, "risk_score": risk},
        "analysed_tests": len(tests),
        "categories": {},
        "clusters": list(clusters),
        "tests": list(tests),
        "actions": list(actions),
        "provenance": {"engine": engine, "llm_provider": "ollama" if model else None, "llm_model": model,
                       "fallback_used": False, "deterministic": engine in ("rules", "ml"),
                       "mode_counts": {engine: len(tests)} if engine and tests else {}, **prov},
    }
    for t in tests:
        key = t["category"] or core.UNCATEGORIZED
        d["categories"][key] = d["categories"].get(key, 0) + 1
    return d


def _c(sig, size, members=(), label=None) -> dict:
    return {"signature": sig, "label": label or sig, "size": size, "member_count": len(members),
            "member_fingerprints": sorted(members)}


# ── hashing ──────────────────────────────────────────────────────────────────


def test_canonical_json_sorts_keys_and_is_compact():
    assert core.canonical_json({"b": 1, "a": [2, {"d": 1, "c": None}]}) == '{"a":[2,{"c":null,"d":1}],"b":1}'
    assert core.sha256_hex("x") == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"


def test_root_cause_hash_ignores_whitespace_and_is_none_when_empty():
    assert core.root_cause_hash("a  b\n c") == core.root_cause_hash(" a b c ")
    assert len(core.root_cause_hash("a")) == 16
    assert core.root_cause_hash("") is None and core.root_cause_hash("   ") is None and core.root_cause_hash(None) is None


def test_the_per_test_hash_is_the_documented_join():
    import hashlib

    expected = hashlib.sha256("\x1f".join(
        ["v1", "PRODUCT_BUG", "85", "1", "0", "0", "1", "ollama", "llama3.1", "llm", "root  cause"]
    ).encode("utf-8")).hexdigest()
    got = core.analysis_content_sha256(
        failure_category="PRODUCT_BUG", confidence_score=85, is_flaky=True, requires_human_review=False,
        backend_error_found=None, pod_issue_found=1, llm_provider="ollama", llm_model="llama3.1",
        analysis_mode="llm", root_cause_summary="root  cause",
    )
    assert got == expected


def test_the_per_test_hash_treats_nulls_as_empty_and_enums_as_values():
    from app.models.postgres import FailureCategory

    a = core.analysis_content_sha256(
        failure_category=FailureCategory.INFRASTRUCTURE, confidence_score=None, is_flaky=None,
        requires_human_review=None, backend_error_found=None, pod_issue_found=None, llm_provider=None,
        llm_model=None, analysis_mode=None, root_cause_summary=None,
    )
    b = core.analysis_content_sha256(
        failure_category="INFRASTRUCTURE", confidence_score=None, is_flaky=False,
        requires_human_review=False, backend_error_found=False, pod_issue_found=False, llm_provider="",
        llm_model="", analysis_mode="", root_cause_summary="",
    )
    assert a == b


def test_the_migration_carries_an_exact_copy_of_the_sql_hash():
    """Migrations never import app code; this keeps the two copies equal."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0205_core", MIGRATION_0205)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.ANALYSIS_CONTENT_SHA256_SQL == core.ANALYSIS_CONTENT_SHA256_SQL
    assert "{a}" in core.ANALYSIS_CONTENT_SHA256_SQL


def test_the_summary_hash_reads_only_the_layers_and_the_fallback_flag():
    base = {"layer1_executive_summary": "x", "layer2_incident_view": {"a": 1}, "fallback_used": False,
            "markdown_report": "# one", "citations": [1], "analysis_count": 3}
    other = dict(base, markdown_report="# two", citations=[], analysis_count=9)
    assert core.summary_content_sha256(base) == core.summary_content_sha256(other)
    assert core.summary_content_sha256(base) != core.summary_content_sha256(dict(base, fallback_used=True))
    assert core.summary_content_sha256(base) != core.summary_content_sha256(dict(base, layer1_executive_summary="y"))


# ── the digest ───────────────────────────────────────────────────────────────


def test_the_digest_has_the_contract_shape():
    d = _digest(
        [_row("fp1"), _row("fp2", cat=None, conf=None, mode="rules", provider=None, model=None),
         _row("fp3", cat="INFRASTRUCTURE", conf=41, flaky=True, review=True)],
        clusters=[_cluster("cl_001", "Timeout in checkout", ["tc-fp1", "tc-fp3"], error="TimeoutError: gw 30s")],
        verdict={"recommendation": "NO_GO", "risk_score": 72, "composite_risk": 68.55, "source": "release_decision"},
        actions=["Fix the gateway", "Fix the gateway", "  Add retries "],
        actions_source="release_decision",
        stage_quality="normal",
    )
    AIDigest.model_validate(d)  # extra="forbid": no stray keys
    assert d["categories"] == {"INFRASTRUCTURE": 1, "PRODUCT_BUG": 1, "UNCATEGORIZED": 1}
    assert list(d["categories"]) == sorted(d["categories"])
    assert d["avg_confidence"] == 60.5 and d["flaky_count"] == 1 and d["requires_review_count"] == 1
    assert d["run"]["pass_rate"] == 93.3
    assert d["verdict"]["composite_risk"] == 68.5 or d["verdict"]["composite_risk"] == 68.6
    assert d["actions"] == ["Add retries", "Fix the gateway"]
    assert [t["test_fingerprint"] for t in d["tests"]] == ["fp1", "fp2", "fp3"]
    assert d["provenance"]["engine"] == "mixed"
    assert d["provenance"]["mode_counts"] == {"llm": 2, "rules": 1}
    assert d["provenance"]["llm_model"] == "llama3.1" and d["provenance"]["deterministic"] is False
    assert d["clusters"][0]["member_fingerprints"] == ["fp1", "fp3"]
    assert d["clusters"][0]["signature"] == "timeouterror: gw #s"
    assert d["content_sha256"] == core.digest_content_sha256(d)


def test_a_member_id_of_another_run_never_reaches_the_digest():
    d = _digest([_row("fp1")], clusters=[_cluster("cl_1", "x", ["tc-fp1", "foreign-id"], size=2)],
                fps={"tc-fp1": "fp1"})
    assert d["clusters"][0]["member_fingerprints"] == ["fp1"]
    assert d["clusters"][0]["member_count"] == 1
    assert d["clusters"][0]["size"] == 2  # the stored size is reported as is


def test_two_clusters_with_one_signature_get_numbered_by_size_then_id():
    d = _digest([_row("a"), _row("b"), _row("c")], clusters=[
        _cluster("cl_2", "Timeout", ["tc-a"], error="Timeout after 30s"),
        _cluster("cl_1", "Timeout", ["tc-b"], error="Timeout after 10s"),
        _cluster("cl_3", "Timeout", ["tc-c", "tc-a"], error="Timeout after 5s"),
    ])
    sigs = {c["cluster_id"]: c["signature"] for c in d["clusters"]}
    assert sigs == {"cl_3": "timeout after #s", "cl_1": "timeout after #s #2", "cl_2": "timeout after #s #3"}


def test_a_cluster_without_error_or_label_text_still_has_a_signature():
    assert core.cluster_signature(None, "") == "unlabelled"
    assert core.cluster_signature("", "Login fails") == "login fails"


def test_shuffled_inputs_yield_a_byte_identical_digest():
    rows = [_row(f"fp{i:03d}", cat=random.choice(["PRODUCT_BUG", "FLAKY", None]), conf=i % 100,
                 cause=f"cause {i % 7}", mode=["llm", "rules"][i % 2],
                 prompt={"react_triage": f"v{i % 3}"})
            for i in range(60)]
    clusters = [_cluster(f"cl_{i}", f"label {i % 4}", [f"tc-fp{j:03d}" for j in range(i, 60, 7)],
                         error=f"Error kind {i % 3}") for i in range(9)]
    baseline = _digest(copy.deepcopy(rows), clusters=copy.deepcopy(clusters), actions=["b", "a", "c"])
    rng = random.Random(7)
    for _ in range(5):
        r2, c2 = copy.deepcopy(rows), copy.deepcopy(clusters)
        rng.shuffle(r2)
        rng.shuffle(c2)
        for c in c2:
            rng.shuffle(c["member_test_ids"])
        again = _digest(r2, clusters=c2, actions=["c", "a", "b"])
        assert core.canonical_json(again) == core.canonical_json(baseline)


def test_capture_fields_are_not_hashed_but_content_is():
    a = _digest(pipeline_run_id="p1", source="stored", captured_at="2026-10-14T10:00:00+00:00")
    b = _digest(pipeline_run_id="p2", source="live", captured_at=None)
    assert a["content_sha256"] == b["content_sha256"]
    c = _digest([_row("fp1", cat="FLAKY"), _row("fp2", cat="INFRASTRUCTURE", conf=40)])
    assert c["content_sha256"] != a["content_sha256"]


def test_whitespace_only_differences_never_read_as_a_change():
    a = _digest([_row("fp1", cause="Gateway timed out after 30s")])
    b = _digest([_row("fp1", cause="  Gateway   timed out\nafter 30s ")])
    assert a["content_sha256"] == b["content_sha256"]


def test_free_text_is_sanitized_before_it_is_stored():
    d = _digest([_row("fp1", cause="login as bob@example.com failed with password=hunter2")],
                clusters=[_cluster("c", "mail bob@example.com", ["tc-fp1"], error="token=abc123secret")],
                actions=["email bob@example.com"])
    blob = json.dumps(d)
    assert "bob@example.com" not in blob and "hunter2" not in blob and "abc123secret" not in blob


def test_a_huge_single_token_root_cause_is_redacted_in_a_bounded_window():
    """Redaction cost grows with one token's length (measured: a 5,000-char
    token took 16 ms); the excerpt is built from a 2,000-char window and a
    token the window cuts is dropped, never shown half-redacted."""
    import time

    blob = "Q" * 50_000
    started = time.perf_counter()
    for _ in range(20):
        excerpt = core.clean_text("prefix " + blob, core.EXCERPT_CHARS)
    assert (time.perf_counter() - started) / 20 < 0.05
    assert excerpt.startswith("prefix") and len(excerpt) <= core.EXCERPT_CHARS
    # A long bearer token redacts down to a few characters and pulls what
    # follows into the excerpt; the e-mail the window cut in half ("bob@example.")
    # no longer looks like one to the patterns, so it must be dropped, not shown.
    pulled = "Bearer " + "A" * 1980 + " bob@example.com and more text " + "w " * 200
    excerpt = core.clean_text(pulled, core.EXCERPT_CHARS)
    assert "bob@" not in excerpt and excerpt.startswith("Bearer [REDACTED]")


def test_the_caps_bound_a_large_digest_under_two_megabytes():
    long_cause = "The checkout service returned HTTP 500 after the tax helper " * 85
    rows = [_row(f"fp{i:05d}", cause=long_cause) for i in range(2100)]
    clusters = [_cluster(f"cl_{i:03d}", f"label {i}", [f"tc-fp{j:05d}" for j in range(400)]) for i in range(60)]
    d = _digest(rows, clusters=clusters, actions=[f"a{i}" * 100 for i in range(80)])
    assert d["analysed_tests"] == 2100 and len(d["tests"]) == core.MAX_TESTS_IN_DIGEST
    assert d["limits"] == {"tests_total": 2100, "tests_truncated": True, "clusters_total": 60, "clusters_truncated": True}
    assert len(d["clusters"]) == core.MAX_CLUSTERS
    assert all(len(c["member_fingerprints"]) <= core.MAX_CLUSTER_MEMBERS for c in d["clusters"])
    assert all(len(t["root_cause_excerpt"]) <= core.EXCERPT_CHARS for t in d["tests"])
    assert len(d["actions"]) == core.MAX_ACTIONS
    assert len(core.canonical_json(d).encode("utf-8")) < 2 * 1024 * 1024


def test_engine_and_determinism_flags():
    assert core.derive_engine({}) is None
    assert core.derive_engine({"rules": 3}) == "rules"
    assert core.derive_engine({"rules": 3, "llm": 1}) == "mixed"
    rules = _digest([_row("a", mode="rules", provider="none", model=None)])
    assert rules["provenance"]["deterministic"] is True and rules["provenance"]["llm_provider"] is None
    fallback = _digest([_row("a", fallback_from="llm", mode="rules")])
    assert fallback["provenance"]["fallback_used"] is True and fallback["provenance"]["fallback_count"] == 1


def test_most_common_model_ties_break_lexicographically():
    d = _digest([_row("a", model="zeta"), _row("b", model="alpha")])
    assert d["provenance"]["llm_model"] == "alpha"


# ── the diff: section 3.2 table ──────────────────────────────────────────────


@pytest.mark.parametrize("frm,to,kinds", [
    (40, 45, []),             # 5: ignored
    (40, 50, ["confidence"]),  # 10: reported
    (40, 55, ["confidence"]),  # 15: reported
    (55, 40, ["confidence"]),  # a drop counts too
    (None, 80, []),            # one side unknown: not a confidence change
])
def test_a_confidence_change_is_material_from_ten_points(frm, to, kinds):
    diff = core.diff_digests(_side([_ref("fp", conf=frm)]), _side([_ref("fp", conf=to)]), mode="versions")
    changed = diff["tests"]["changed"]
    assert [c["kinds"] for c in changed] == ([kinds] if kinds else [])
    if kinds:
        assert changed[0]["confidence"] == {"from": frm, "to": to, "delta": to - frm}
        assert changed[0]["material"] is True


def test_tests_are_matched_by_fingerprint_never_by_name():
    diff = core.diff_digests(_side([_ref("fp", name="old name")]), _side([_ref("fp", name="new name")]), mode="versions")
    assert diff["tests"]["counts"] == {"added": 0, "removed": 0, "changed": 0, "material_changed": 0}
    assert diff["tests"]["persisting_unchanged"] == 1


def test_category_flaky_and_root_cause_kinds():
    a = _side([_ref("a", cat="INFRASTRUCTURE"), _ref("b", cat=None), _ref("c", flaky=False), _ref("d", cause="old")])
    b = _side([_ref("a", cat="PRODUCT_BUG"), _ref("b", cat="FLAKY"), _ref("c", flaky=True), _ref("d", cause="new")])
    diff = core.diff_digests(a, b, mode="versions")
    by = {c["test_fingerprint"]: c for c in diff["tests"]["changed"]}
    assert by["a"]["category"] == {"from": "INFRASTRUCTURE", "to": "PRODUCT_BUG"}
    assert by["b"]["kinds"] == ["category"]
    assert by["c"]["flaky"] == {"from": False, "to": True}
    assert by["d"]["kinds"] == ["root_cause"] and by["d"]["material"] is False
    assert by["d"]["root_cause_excerpt"] == {"from": "old", "to": "new"}
    assert diff["tests"]["counts"]["changed"] == 4
    assert diff["tests"]["counts"]["material_changed"] == 3
    # material rows first, the reworded one last
    assert diff["tests"]["changed"][-1]["test_fingerprint"] == "d"


def test_a_reworded_root_cause_alone_is_listed_but_not_material():
    diff = core.diff_digests(_side([_ref("a", cause="x")]), _side([_ref("a", cause="y")]), mode="cross_run")
    assert diff["tests"]["counts"]["material_changed"] == 0
    assert diff["has_changes"] is True  # the section is non-empty


def test_added_and_removed_tests_and_their_order():
    a = _side([_ref("gone", cat="FLAKY", conf=30)])
    b = _side([_ref("z", cat="PRODUCT_BUG", conf=50), _ref("y", cat="PRODUCT_BUG", conf=90), _ref("x", cat=None, conf=10)])
    diff = core.diff_digests(a, b, mode="cross_run")
    assert [t["test_fingerprint"] for t in diff["tests"]["added"]] == ["x", "y", "z"]
    assert [t["test_fingerprint"] for t in diff["tests"]["removed"]] == ["gone"]
    assert diff["tests"]["removed"][0]["test_id"] == "id-gone"


def test_a_change_list_over_two_hundred_is_cut_but_counted():
    a = _side([_ref(f"t{i:03d}", cat="INFRASTRUCTURE") for i in range(250)])
    b = _side([_ref(f"t{i:03d}", cat="PRODUCT_BUG") for i in range(250)])
    diff = core.diff_digests(a, b, mode="versions")
    assert len(diff["tests"]["changed"]) == core.MAX_DIFF_TEST_CHANGES
    assert diff["tests"]["counts"]["changed"] == 250 and diff["tests"]["truncated"] is True


def test_clusters_match_by_signature_first_and_a_rename_is_the_same_cluster():
    a = _side(clusters=[_c("sig-a", 3, ["1", "2", "3"], label="Old label")])
    b = _side(clusters=[_c("sig-a", 3, ["7", "8", "9"], label="New label")])
    diff = core.diff_digests(a, b, mode="versions")["clusters"]
    assert diff["added"] == [] and diff["removed"] == []
    (m,) = diff["matched"]
    assert m["matched_by"] == "signature" and m["resized"] is False and m["label_to"] == "New label"


def test_a_resized_cluster_reports_its_delta():
    diff = core.diff_digests(_side(clusters=[_c("s", 3)]), _side(clusters=[_c("s", 5)]), mode="versions")
    (m,) = diff["clusters"]["matched"]
    assert (m["delta"], m["resized"]) == (2, True) and diff["clusters"]["counts"]["resized"] == 1


def test_a_cluster_split_matches_the_overlapping_half_and_adds_the_other():
    a = _side(clusters=[_c("big", 6, ["1", "2", "3", "4", "5", "6"])])
    b = _side(clusters=[_c("left", 3, ["1", "2", "3"]), _c("right", 3, ["4", "5", "6"])])
    diff = core.diff_digests(a, b, mode="versions")["clusters"]
    assert diff["counts"] == {"added": 1, "removed": 0, "matched": 1, "resized": 1}
    (m,) = diff["matched"]
    assert m["matched_by"] == "overlap" and m["jaccard"] == 0.5 and m["signature_to"] == "left"
    assert diff["added"][0]["signature"] == "right"


@pytest.mark.parametrize("members_a,members_b,matched", [
    (["1", "2", "3"], ["1", "2", "4"], True),   # 2/4 = 0.5
    (["1", "2", "3"], ["1", "4", "5"], False),  # 1/5 = 0.2
    ([], [], False),                            # empty never matches by overlap
])
def test_overlap_threshold_is_half(members_a, members_b, matched):
    diff = core.diff_digests(_side(clusters=[_c("x", 3, members_a)]), _side(clusters=[_c("y", 3, members_b)]),
                             mode="versions")["clusters"]
    assert (diff["counts"]["matched"] == 1) is matched
    if not matched:
        assert diff["counts"]["added"] == 1 and diff["counts"]["removed"] == 1


def test_overlap_pairs_are_taken_greedily_by_jaccard_then_signature():
    a = _side(clusters=[_c("a1", 4, ["1", "2", "3", "4"]), _c("a2", 4, ["1", "2", "3", "5"])])
    b = _side(clusters=[_c("b1", 4, ["1", "2", "3", "4"])])
    diff = core.diff_digests(a, b, mode="versions")["clusters"]
    (m,) = diff["matched"]
    assert (m["signature_from"], m["jaccard"]) == ("a1", 1.0)
    assert [c["signature"] for c in diff["removed"]] == ["a2"]


def test_cluster_lists_are_sorted_by_size_then_signature():
    a = _side(clusters=[_c("r1", 2), _c("r2", 9)])
    b = _side(clusters=[_c("n1", 1), _c("n3", 5), _c("n2", 5)])
    diff = core.diff_digests(a, b, mode="cross_run")["clusters"]
    assert [c["signature"] for c in diff["added"]] == ["n2", "n3", "n1"]
    assert [c["signature"] for c in diff["removed"]] == ["r2", "r1"]


@pytest.mark.parametrize("frm,to,direction", [
    ("GO", "NO_GO", "worse"), ("CONDITIONAL_GO", "NO_GO", "worse"), ("NO_GO", "GO", "better"),
    ("GO", "GO", "same"), (None, "GO", "unknown"), ("GO", None, "unknown"), ("GO", "MAYBE", "unknown"),
])
def test_verdict_direction(frm, to, direction):
    assert core.verdict_direction(frm, to) == direction


def test_the_verdict_block_and_risk_delta():
    diff = core.diff_digests(_side(verdict="CONDITIONAL_GO", risk=55), _side(verdict="NO_GO", risk=72), mode="versions")
    assert diff["verdict"] == {"changed": True, "from": "CONDITIONAL_GO", "to": "NO_GO", "direction": "worse",
                               "from_risk": 55, "to_risk": 72, "risk_delta": 17}


def test_category_counts_sorted_by_size_of_the_change():
    a = _side([_ref("1", cat="PRODUCT_BUG"), _ref("2", cat="INFRASTRUCTURE"), _ref("3", cat="INFRASTRUCTURE")])
    b = _side([_ref("1", cat="PRODUCT_BUG"), _ref("2", cat="PRODUCT_BUG"), _ref("3", cat="PRODUCT_BUG"), _ref("4", cat="FLAKY")])
    cats = core.diff_digests(a, b, mode="versions")["categories"]
    assert cats["changed"] == [
        {"category": "INFRASTRUCTURE", "from": 2, "to": 0, "delta": -2},
        {"category": "PRODUCT_BUG", "from": 1, "to": 3, "delta": 2},
        {"category": "FLAKY", "from": 0, "to": 1, "delta": 1},
    ]
    assert (cats["from_total"], cats["to_total"]) == (3, 4)


def test_an_engine_change_is_reported_first_and_worded_per_mode():
    a = _side([_ref("a")], engine="rules", model=None)
    b = _side([_ref("a")], engine="llm", model="llama3.1")
    cross = core.diff_digests(a, b, mode="cross_run")["provenance"]
    assert cross["engine"] == {"from": "rules", "to": "llm"}
    assert cross["note"] == ("Baseline analysed by rules; current by llm. "
                             "Category changes may reflect the engine, not the product.")
    versions = core.diff_digests(a, b, mode="versions")["provenance"]
    assert versions["note"] == "The analysis engine changed between these versions (rules -> llm)."
    assert versions["engine_only"] is True  # nothing else changed
    assert list(core.diff_digests(a, b, mode="versions"))[:5] == ["schema_version", "mode", "identical", "has_changes", "provenance"]


def test_a_material_change_is_not_engine_only():
    a = _side([_ref("a", cat="INFRASTRUCTURE")], engine="rules", model=None)
    b = _side([_ref("a", cat="PRODUCT_BUG")], engine="llm")
    assert core.diff_digests(a, b, mode="cross_run")["provenance"]["engine_only"] is False


def test_reworded_root_causes_do_not_spoil_engine_only():
    a = _side([_ref("a", cause="x")], engine="rules", model=None)
    b = _side([_ref("a", cause="y")], engine="llm")
    assert core.diff_digests(a, b, mode="versions")["provenance"]["engine_only"] is True


def test_identical_digests_have_no_changes():
    d = _digest()
    diff = core.diff_digests(d, copy.deepcopy(d), mode="versions")
    assert diff["identical"] is True and diff["has_changes"] is False
    assert diff["tests"]["changed"] == [] and diff["clusters"]["added"] == [] and diff["categories"]["changed"] == []
    assert diff["provenance"]["changed"] is False and diff["provenance"]["note"] is None


def test_actions_added_and_removed():
    diff = core.diff_digests(_side(actions=["keep", "old"]), _side(actions=["keep", "new"]), mode="versions")
    assert diff["actions"] == {"added": ["new"], "removed": ["old"]}


def test_an_older_or_empty_stored_shape_never_raises():
    diff = core.diff_digests({}, {"verdict": None, "tests": None}, mode="versions")
    assert diff["has_changes"] is False and diff["verdict"]["direction"] == "unknown"
    with pytest.raises(ValueError):
        core.diff_digests({}, {}, mode="sideways")


# ── derived summaries ────────────────────────────────────────────────────────


def _rich_diff() -> dict:
    a = _side([_ref("pay", cat="INFRASTRUCTURE", conf=40), _ref("old", cat="FLAKY")],
              clusters=[_c("gone", 6, ["x"], label="Resolved thing")], verdict="GO", risk=35, engine="rules", model=None)
    b = _side([_ref("pay", cat="PRODUCT_BUG", conf=85, name="test_pay")],
              clusters=[_c("new", 4, ["y"], label="Timeout in checkout — café")], verdict="NO_GO", risk=72)
    return core.diff_digests(a, b, mode="cross_run")


def test_ai_changes_lines_are_ordered_ascii_and_deterministic():
    lines = core.build_ai_changes(_rich_diff())
    assert lines == [
        "Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.",
        "Verdict GO -> NO_GO (risk 35 -> 72)",
        "New root cause: Timeout in checkout ? caf? (4 tests)",
        "Resolved: Resolved thing (6 tests)",
        "test_pay: INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)",
    ]
    assert all(line.isascii() for line in lines)
    assert core.build_ai_changes(_rich_diff()) == lines


def test_ai_changes_are_cut_with_a_count_of_the_rest():
    lines = core.build_ai_changes(_rich_diff(), limit=3)
    assert lines == [
        "Baseline analysed by rules; current by llm. Category changes may reflect the engine, not the product.",
        "Verdict GO -> NO_GO (risk 35 -> 72)",
        "+3 more changes",
    ]


def test_an_unchanged_verdict_reports_a_large_risk_move_only():
    big = core.diff_digests(_side(verdict="GO", risk=20), _side(verdict="GO", risk=30), mode="versions")
    small = core.diff_digests(_side(verdict="GO", risk=20), _side(verdict="GO", risk=29), mode="versions")
    assert core.build_ai_changes(big) == ["Risk 20 -> 30"]
    assert core.build_ai_changes(small) == []


def test_the_compare_brief_validates_and_keeps_only_material_tests():
    diff = _rich_diff()
    brief = core.brief_from_diff(diff)
    AIAnalysisDiffBrief.model_validate(brief)
    assert brief["available"] is True and brief["left_ref"] == "live"
    assert brief["counts"]["clusters_added"] == 1 and brief["counts"]["tests_removed"] == 1
    assert brief["test_changes"] == [{
        "test_name": "test_pay", "suite_name": "s", "category_from": "INFRASTRUCTURE", "category_to": "PRODUCT_BUG",
        "confidence_from": 40, "confidence_to": 85,
    }]
    assert brief["ai_changes"] == core.build_ai_changes(diff)
    AIAnalysisDiffBrief.model_validate({"available": False, "reason": "ai_history_unavailable"})


def test_the_brief_caps_its_lists():
    a = _side([_ref(f"t{i:03d}", cat="INFRASTRUCTURE") for i in range(80)])
    b = _side([_ref(f"t{i:03d}", cat="PRODUCT_BUG") for i in range(80)],
              clusters=[_c(f"c{i:02d}", 2) for i in range(15)])
    brief = core.brief_from_diff(core.diff_digests(a, b, mode="cross_run"))
    assert len(brief["test_changes"]) == 50 and len(brief["clusters_added"]) == 10


def test_digest_side_is_the_compact_column():
    d = _digest([_row("a")], clusters=[_cluster(f"c{i}", f"L{i}", ["tc-a"], size=i + 1) for i in range(7)],
                verdict={"recommendation": "GO", "risk_score": 10, "decision_report_version": 2},
                source="stored", captured_at="2026-10-14T10:00:00+00:00")
    side = core.digest_side(d)
    DigestSide.model_validate(side)
    assert [c["size"] for c in side["top_clusters"]] == [7, 6, 5, 4, 3]
    assert side["decision_report_version"] == 2 and side["generated_at"] == "2026-10-14T10:00:00+00:00"
    assert core.digest_side(_digest())["generated_at"] is None  # a live side


def test_not_analysed_means_no_tests_no_clusters_no_verdict():
    assert core.is_not_analysed(_digest([])) is True
    assert core.is_not_analysed(_digest([], verdict={"recommendation": "GO"})) is False
    assert core.is_not_analysed(_digest()) is False


def test_meta_and_provenance_projections():
    d = _digest(verdict={"recommendation": "NO_GO", "risk_score": 70}, clusters=[_cluster("c", "x", ["tc-fp1"])])
    assert core.meta_from_digest(d) == {
        "verdict": "NO_GO", "risk_score": 70, "analysed_tests": 2, "cluster_count": 1,
        "categories": {"INFRASTRUCTURE": 1, "PRODUCT_BUG": 1}, "headline": None,
        "decision_report_ref": None, "summary_version": None,
    }
    assert core.provenance_from_digest(d)["analysis_mode"] == "llm"
    summary = {"executive_summary": "  Two   failures " + "x" * 300, "analysis_count": 2, "fallback_used": True,
               "summary_provenance": {"model_config_snapshot": {"provider": "ollama", "model": "m"},
                                      "prompt_versions": {"s": "v1"}}}
    meta = core.meta_from_summary(summary)
    assert meta["headline"].startswith("Two failures x") and len(meta["headline"]) == 200
    assert meta["analysed_tests"] == 2
    prov = core.provenance_from_summary(summary)
    assert prov == {"analysis_mode": "deterministic", "llm_provider": "ollama", "llm_model": "m",
                    "fallback_used": True, "deterministic": True, "prompt_versions": {"s": "v1"}, "source": "stored"}


# ── retention: plan_prune (D41) ──────────────────────────────────────────────


def _v(n: int, pipeline: str | None = None) -> VersionRef:
    return VersionRef(id=f"id{n}", version=n, pipeline_run_id=pipeline)


@pytest.mark.parametrize("versions,cap,cited,expected", [
    ([_v(i) for i in range(1, 6)], 3, set(), ["id2", "id3"]),                       # keeps v1, v4, v5
    ([_v(1), _v(2, "p2"), _v(3), _v(4), _v(5)], 3, {"p2"}, ["id3", "id4"]),         # cited v2 kept
    ([_v(i) for i in range(1, 4)], 3, set(), []),                                   # cap >= count
    ([_v(5), _v(1), _v(3), _v(2), _v(4)], 3, set(), ["id2", "id3"]),                 # unsorted input
    ([_v(i) for i in range(1, 6)], 1, set(), ["id2", "id3", "id4"]),                 # v1 and the newest never
    ([_v(1), _v(2, "a"), _v(3, "b"), _v(4), _v(5)], 2, {"a", "b"}, ["id4"]),          # protected exceed cap
    ([_v(1), _v(2)], 1, set(), []),                    # a cap of 1 never deletes the version just written
    ([], 3, set(), []),
])
def test_plan_prune(versions, cap, cited, expected):
    assert core.plan_prune(versions, cap=cap, cited_pipeline_ids=cited) == expected


def test_plan_prune_protects_the_lowest_version_even_after_v1_is_gone():
    assert core.plan_prune([_v(4), _v(5), _v(6), _v(7)], cap=2, cited_pipeline_ids=set()) == ["id5", "id6"]
    assert core.plan_prune([_v(4), _v(5), _v(6), _v(7)], cap=3, cited_pipeline_ids=set()) == ["id5"]


def test_clamp_cap():
    assert core.clamp_cap(None, 20) == 20
    assert core.clamp_cap(0, 20) == 20 and core.clamp_cap(101, 10) == 10 and core.clamp_cap(3, 20) == 3


# ── goldens ──────────────────────────────────────────────────────────────────


def _golden_inputs():
    v1 = _digest(
        [_row("fp_pay", tid="aaaaaaaa-0000-4000-8000-000000000001", name="test_pay", cat="INFRASTRUCTURE", conf=40,
              cause="Connection refused to payment-gateway", mode="rules", provider=None, model=None),
         _row("fp_cart", tid="aaaaaaaa-0000-4000-8000-000000000002", name="test_cart", cat="INFRASTRUCTURE", conf=45,
              cause="Connection refused to payment-gateway", mode="rules", provider=None, model=None)],
        clusters=[_cluster("cl_001", "Payment gateway unreachable",
                           ["aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002"],
                           error="ConnectionError: refused 10.0.0.12:443")],
        verdict={"recommendation": "CONDITIONAL_GO", "risk_score": 55, "composite_risk": 52.04,
                 "source": "release_decision"},
        actions=["Restore payment-gateway connectivity"], actions_source="release_decision",
        pipeline_run_id="bbbbbbbb-0000-4000-8000-000000000001", source="stored",
        captured_at="2026-10-14T10:00:00+00:00", stage_quality="normal", carried_over_tests=0,
    )
    v2 = _digest(
        [_row("fp_pay", tid="aaaaaaaa-0000-4000-8000-000000000001", name="test_pay", cat="PRODUCT_BUG", conf=85,
              cause="Checkout total rounds the tax twice"),
         _row("fp_cart", tid="aaaaaaaa-0000-4000-8000-000000000002", name="test_cart", cat="PRODUCT_BUG", conf=80,
              cause="Checkout total rounds the tax twice"),
         _row("fp_login", tid="aaaaaaaa-0000-4000-8000-000000000003", name="test_login", cat="PRODUCT_BUG", conf=70,
              cause="Session cookie missing SameSite")],
        clusters=[_cluster("cl_001", "Timeout in checkout",
                           ["aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002",
                            "aaaaaaaa-0000-4000-8000-000000000003"],
                           error="AssertionError: total 100.10 != 100.00")],
        verdict={"recommendation": "NO_GO", "risk_score": 72, "composite_risk": 68.51,
                 "source": "decision_report", "decision_report_version": 2},
        actions=["Fix tax rounding in checkout"], actions_source="decision_report",
        pipeline_run_id="bbbbbbbb-0000-4000-8000-000000000002", source="stored",
        captured_at="2026-10-14T11:00:00+00:00", stage_quality="normal", carried_over_tests=0,
    )
    return v1, v2


@pytest.mark.parametrize("name", ["digest", "versions-diff", "cross-run-diff"])
def test_goldens(name):
    v1, v2 = _golden_inputs()
    actual = {
        "digest": v2,
        "versions-diff": core.diff_digests(v1, v2, mode="versions"),
        "cross-run-diff": core.diff_digests(v1, v2, mode="cross_run"),
    }[name]
    path = GOLDENS / f"{name}.golden.json"
    text = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")
    assert json.loads(path.read_text(encoding="utf-8")) == actual


def test_more_tests_analysed_by_the_same_engine_is_not_a_provenance_change():
    """Regression (W03 local walk): rules on 2 tests -> rules on 3 tests set provenance.changed,
    and the previous-run card said "The analysis engine changed between these analyses"."""
    same = {"engine": "rules", "llm_provider": None, "llm_model": "rules_engine", "fallback_used": False,
            "deterministic": True}
    p = core._diff_provenance({**same, "mode_counts": {"rules": 2}}, {**same, "mode_counts": {"rules": 3}}, "cross_run")
    assert p["changed"] is False and p["note"] is None
    assert p["mode_counts"] == {"from": {"rules": 2}, "to": {"rules": 3}}  # still reported
    mixed = core._diff_provenance(
        {**same, "mode_counts": {"rules": 2}}, {**same, "mode_counts": {"rules": 2, "llm": 1}}, "cross_run",
    )
    assert mixed["changed"] is True
