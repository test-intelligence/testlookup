"""Prompt registry + enforced manifest gate (Agentic plan AI-F2).

Pins the whole contract:
  * byte-identity — every prompt moved into the registry hashes to the SAME
    sha256[:12] as the original inline constant it replaced (hashes frozen
    below at move time, 2026-07-15);
  * registry ⇄ manifest sync (``check_manifest``) and manifest ⇄ attestation
    sync (``check_attestation``) are green on this tree;
  * drift is detected — a hash mismatch, a version mismatch, a stale manifest
    entry, and a manifest change without a fresh attestation all fail;
  * the quality-gate mirror (``scripts/quality_gate.py`` guard
    ``ai.prompt-manifest-sync``) agrees with the registry implementation;
  * call sites render byte-identically through the registry (template
    ``.format`` equivalence for the two runtime-composed prompts).
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from app.services import prompt_registry as pr  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Frozen byte-identity pins ────────────────────────────────────────────────
# sha256(text)[:12] of the ORIGINAL inline constants, captured before the move
# (AI-F2, 2026-07-15). If one of these fails, the "zero behavior change" claim
# of the registry migration is broken — do not update these without
# understanding why.
_ORIGINAL_HASHES = {
    "chat_system": "b52c5b2cf229",
    "summary_system": "3b2f2b5fde10",
    "summary_executive": "9885ab3d7268",
    "summary_incident_view": "470a2488b718",
    "summary_evidence_pack": "54b0e91c81d9",
    "summary_action_plan": "ecf7e2106438",
    "summary_renderer_system": "42fc12956d2f",
    "summary_renderer_developer": "58d153d90e99",
    "summary_renderer_manager": "fb02a6a74429",
    "run_compare_system": "bbba41240c3f",
    "run_compare_report": "5272dd391090",
    "release_risk_reasoning": "a152fb254091",
    "regression_watchman_classify": "2ac1510063fd",
    "defect_promotion_ticket": "45d88cc4d33b",
    "fast_classifier_system": "9d46ffbbed7b",
    "finetune_reasoning_system": "3ea37c087b6b",
    "test_case_generate": "fb6a09db0b78",
    "test_case_review": "43f1dfbc6c86",
    "test_case_coverage": "16988c2ae516",
    "test_case_strategy": "2070533050ae",
    "test_case_plan_optimizer": "97d09e4d78eb",
}
# MCP template texts, reconstructed as {placeholder} templates from the
# original f-strings — same bytes once rendered (placeholders were plain
# ``{name}`` interpolations with no format specs).
_ORIGINAL_MCP_HASHES = {
    "mcp.investigate_failure": "590cb27ee41f",
    "mcp.release_readiness_report": "952071abb759",
    "mcp.weekly_quality_digest": "b96eda2d909a",
    "mcp.flakiness_investigation": "479ef097d997",
    "mcp.defect_triage_session": "670026c0afac",
    "mcp.suite_health_check": "b6bfbd9fe1cd",
}
# The ORIGINAL react_triage (v1) hash — v2 deliberately differs (AI-F3 recall).
_REACT_TRIAGE_V1_HASH = "e8d6ac6b354e"


# ── Byte identity ────────────────────────────────────────────────────────────


def test_moved_prompts_are_byte_identical_to_originals():
    for pid, original_hash in _ORIGINAL_HASHES.items():
        p = pr.get_prompt(pid)
        assert p.content_hash == original_hash, (
            f"{pid} no longer matches the pre-move constant "
            f"({p.content_hash} != {original_hash})"
        )
        assert p.version == 1, f"{pid} bumped without updating this pin"


def test_react_triage_is_deliberate_v2():
    p = pr.get_prompt("react_triage")
    assert p.version == 2
    assert p.content_hash != _REACT_TRIAGE_V1_HASH
    assert "recall_similar_failures" in p.text
    assert "six investigation tools" in p.text
    # v1's structural anchors survived the edit
    assert "Final Answer: {{valid JSON object as specified above}}" in p.text
    assert "{agent_scratchpad}" in p.text


def test_mcp_templates_are_byte_identical_to_originals():
    mcp = pr._load_mcp_templates()
    if not mcp:
        pytest.skip("mcp/prompts/templates.py not present in this checkout")
    for pid, original_hash in _ORIGINAL_MCP_HASHES.items():
        assert mcp[pid]["content_hash"] == original_hash, pid
        assert mcp[pid]["version"] == 1


# ── Registry ⇄ manifest ⇄ attestation sync (the enforced gate, unit level) ──


def test_manifest_in_sync_with_registry():
    assert pr.check_manifest() == []


def test_attestation_matches_current_manifest_and_passes():
    assert pr.check_attestation() == []
    attestation = pr.load_attestation()
    assert attestation["verdict"] == "PASS"
    assert attestation["manifest_digest"] == pr.manifest_digest(
        pr.load_manifest()["prompts"]
    )


def test_manifest_pins_every_registered_prompt():
    manifest = pr.load_manifest()
    pinned = set(manifest["prompts"])
    assert set(pr.registry_prompt_ids()) <= pinned
    for pid in pr.registry_prompt_ids():
        assert manifest["prompts"][pid]["content_hash"] == pr.get_prompt(pid).content_hash


def _write_manifest_copy(tmp_path: Path, mutate) -> Path:
    manifest = pr.load_manifest()
    mutate(manifest)
    path = tmp_path / "prompt_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_hash_drift_is_detected(tmp_path):
    path = _write_manifest_copy(
        tmp_path,
        lambda m: m["prompts"]["react_triage"].__setitem__("content_hash", "deadbeef0000"),
    )
    problems = pr.check_manifest(manifest_path=path)
    assert any("react_triage" in p and "drifted" in p for p in problems)


def test_version_drift_is_detected(tmp_path):
    path = _write_manifest_copy(
        tmp_path,
        lambda m: m["prompts"]["chat_system"].__setitem__("version", 99),
    )
    problems = pr.check_manifest(manifest_path=path)
    assert any("chat_system" in p and "version" in p for p in problems)


def test_stale_manifest_entry_is_detected(tmp_path):
    path = _write_manifest_copy(
        tmp_path,
        lambda m: m["prompts"].__setitem__(
            "ghost_prompt", {"version": 1, "content_hash": "abcabcabcabc"},
        ),
    )
    problems = pr.check_manifest(manifest_path=path)
    assert any("ghost_prompt" in p and "stale" in p for p in problems)


def test_manifest_change_without_attestation_fails(tmp_path):
    # A legitimate-looking manifest edit (correct hash for a hypothetical v3)
    # still fails the attestation check because the digest moved.
    def bump(m):
        m["prompts"]["chat_system"]["version"] = 2

    path = _write_manifest_copy(tmp_path, bump)
    problems = pr.check_attestation(manifest_path=path)
    assert any("without a fresh eval-gate attestation" in p for p in problems)


def test_missing_attestation_fails(tmp_path):
    problems = pr.check_attestation(attestation_path=tmp_path / "missing.json")
    assert problems and "--attest" in problems[0]


def test_failed_verdict_fails(tmp_path):
    attestation = pr.load_attestation()
    attestation["verdict"] = "FAIL"
    path = tmp_path / "prompt_manifest_eval.json"
    path.write_text(json.dumps(attestation), encoding="utf-8")
    problems = pr.check_attestation(attestation_path=path)
    assert any("not PASS" in p for p in problems)


# ── Quality-gate mirror (scripts/quality_gate.py) ────────────────────────────


def _load_quality_gate_module():
    path = REPO_ROOT / "scripts" / "quality_gate.py"
    if not path.exists():
        pytest.skip("scripts/quality_gate.py not present")
    spec = importlib.util.spec_from_file_location("quality_gate_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_quality_gate_guard_green_on_this_tree():
    qg = _load_quality_gate_module()
    assert qg._ai_prompt_manifest_sync() == []


def test_quality_gate_guard_detects_drift_and_missing_attestation(tmp_path):
    qg = _load_quality_gate_module()
    tampered = _write_manifest_copy(
        tmp_path,
        lambda m: m["prompts"]["react_triage"].__setitem__("content_hash", "deadbeef0000"),
    )
    violations = qg._ai_prompt_manifest_sync(manifest_path=tampered)
    messages = [v.message for v in violations]
    assert any("react_triage" in m and "drifted" in m for m in messages)
    # Attestation digest no longer matches the tampered manifest either.
    assert any("attestation" in m for m in messages)

    violations = qg._ai_prompt_manifest_sync(
        attestation_path=tmp_path / "missing_attestation.json",
    )
    assert any("attestation missing" in v.message for v in violations)


def test_quality_gate_digest_mirrors_registry():
    qg = _load_quality_gate_module()
    prompts = pr.load_manifest()["prompts"]
    assert qg._prompt_manifest_digest(prompts) == pr.manifest_digest(prompts)
    assert qg._prompt_content_hash("abc") == pr.content_hash("abc")


def test_quality_gate_guard_is_registered():
    qg = _load_quality_gate_module()
    assert "ai.prompt-manifest-sync" in qg.GUARD_BY_NAME


# ── Public API shape ─────────────────────────────────────────────────────────


def test_get_prompt_unknown_id_raises_with_known_ids():
    with pytest.raises(KeyError) as exc:
        pr.get_prompt("nope_not_a_prompt")
    assert "react_triage" in str(exc.value)


def test_version_tags_have_stable_shape():
    tag_re = re.compile(r"^v\d+:[0-9a-f]{12}$")
    versions = pr.registry_versions()
    assert len(versions) >= 24
    for pid, tag in versions.items():
        assert tag_re.match(tag), (pid, tag)
    used = pr.prompt_versions_used("react_triage", "fast_classifier_system")
    assert set(used) == {"react_triage", "fast_classifier_system"}
    assert used["react_triage"] == pr.get_prompt("react_triage").version_tag


def test_registry_digest_is_stable_sha256():
    d1, d2 = pr.registry_digest(), pr.registry_digest()
    assert d1 == d2
    assert re.fullmatch(r"[0-9a-f]{64}", d1)


# ── Call-site equivalence ────────────────────────────────────────────────────


def test_call_sites_import_registry_texts():
    from app.services import agent as agent_mod
    from app.services import summary_renderer as sr
    from app.services.training import classifier as tc

    assert agent_mod.SYSTEM_PROMPT == pr.get_prompt_text("react_triage")
    assert sr._SYSTEM_PROMPT == pr.get_prompt_text("summary_renderer_system")
    assert sr._DEVELOPER_PROMPT == pr.get_prompt_text("summary_renderer_developer")
    assert sr._MANAGER_PROMPT == pr.get_prompt_text("summary_renderer_manager")
    assert tc._CLASSIFIER_SYSTEM == pr.get_prompt_text("fast_classifier_system")


def test_anomaly_narrative_template_renders_like_old_fstring():
    pass_rate, total_tests = 87.456, 123
    descriptions = "- [HIGH] db down\n- [LOW] slow test"
    expected = (
        f"Summarise these test anomalies in 2-3 sentences for an engineering team.\n"
        f"Pass rate: {pass_rate:.1f}%  |  Total tests: {total_tests}\n"
        f"Findings:\n{descriptions}\n\n"
        f"Mention the most critical finding first and suggest a concrete next action."
    )
    got = pr.get_prompt_text("anomaly_narrative").format(
        pass_rate=pass_rate, total_tests=total_tests, descriptions=descriptions,
    )
    assert got == expected


def test_chat_compression_template_renders_like_old_fstring():
    transcript = "USER: why does {weird} test fail\nASSISTANT: because"
    expected = (
        "Summarise the following QA analysis chat conversation in 4-6 bullet points. "
        "Preserve: specific test names, build numbers, pass rates, failure categories, "
        "key findings, and any decisions or actions discussed. Be concise.\n\n"
        f"{transcript}"
    )
    got = pr.get_prompt_text("chat_compression").format(transcript=transcript)
    assert got == expected


def test_summary_prompt_versions_map_uses_registry_tags():
    pytest.importorskip("langgraph")
    from app.agents import summary_agent as sa

    assert sa._SUMMARY_PROMPT_VERSIONS["system"] == pr.get_prompt("summary_system").version_tag
    assert sa._SYSTEM_PROMPT == pr.get_prompt_text("summary_system")
    assert sa._ACTION_PLAN_PROMPT == pr.get_prompt_text("summary_action_plan")
