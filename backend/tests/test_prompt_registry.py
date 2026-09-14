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
    "summary_system": "3b2f2b5fde10",
    "summary_executive": "9885ab3d7268",
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
# The ORIGINAL summary layer hashes — v2 deliberately differs (F-3): each layer
# now returns ``evidence_ids`` so citations resolve against a server-built
# catalogue instead of being recovered by verbatim text matching on one layer.
_SUMMARY_LAYER_V1_HASHES = {
    "summary_incident_view": "470a2488b718",
    "summary_evidence_pack": "54b0e91c81d9",
    "summary_action_plan": "ecf7e2106438",
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


def test_summary_layers_are_deliberate_v2():
    """F-3: the three claim-bearing layers gained an evidence_ids contract.

    Pinned like every other deliberate bump — the change is visible as a diff
    here, and v1's structural anchors are asserted to have survived so the edit
    cannot quietly rewrite the layer's shape while adding citations.
    """
    anchors = {
        "summary_incident_view": '"release_impact": "GO | CONDITIONAL_GO | NO_GO"',
        "summary_evidence_pack": '"top_stack_traces"',
        "summary_action_plan": '"immediate_mitigation"',
    }
    for pid, v1_hash in _SUMMARY_LAYER_V1_HASHES.items():
        p = pr.get_prompt(pid)
        assert p.version == 2, f"{pid} version pin is stale"
        assert p.content_hash != v1_hash
        assert '"evidence_ids"' in p.text, f"{pid} lost the citation contract"
        assert "Never invent an id." in p.text
        assert anchors[pid] in p.text, f"{pid} lost a v1 structural anchor"


# The ORIGINAL chat_system (v1) hash — v2 deliberately differs (AI-6 copilot:
# the single-shot prompt must not fabricate tool activity now that some chat
# answers carry a real tool trace).
_CHAT_SYSTEM_V1_HASH = "b52c5b2cf229"


def test_chat_system_is_deliberate_v2():
    p = pr.get_prompt("chat_system")
    assert p.version == 2
    assert p.content_hash != _CHAT_SYSTEM_V1_HASH
    assert "How I looked this up" in p.text
    # v1's structural anchors survived the edit
    assert "- Current date/time (UTC): {now}" in p.text
    assert "- Query focus: {intent_label}" in p.text
    assert "Ground every answer in the retrieved context below" in p.text


def test_chat_copilot_react_prompt_registered():
    p = pr.get_prompt("chat_copilot_react")
    assert p.version == 1
    # ReAct structural anchors the loop parser depends on
    for anchor in ("{tools}", "{tool_names}", "{input}", "{agent_scratchpad}",
                   "Action Input:", "Final Answer:"):
        assert anchor in p.text
    # Bound-loop honesty rules
    assert "budget is exhausted" in p.text
    assert "read-only" in p.text


def test_react_triage_is_deliberate_v3():
    """v3 moves citation identifiers server-side: the model must return an
    EMPTY ``evidence_references`` array, and citations are derived from the
    tool observations that actually executed. That is an anti-fabrication
    guarantee, so pin it — a prompt that invites the model to author its own
    reference_ids has regressed the contract."""
    p = pr.get_prompt("react_triage")
    assert p.version == 3
    assert p.content_hash != _REACT_TRIAGE_V1_HASH
    # v3's reason for existing
    assert '"evidence_references": []' in p.text
    assert "MUST be an empty array" in p.text
    assert "never" in p.text and "invent citation identifiers" in p.text
    # v2's memory-recall rule survived the edit
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
    assert attestation["verdict"] == "pass"
    assert attestation["manifest_digest"] == pr.manifest_digest(
        pr.load_manifest()["prompts"]
    )
    assert {gate["status"] for gate in attestation["gate_results"]} <= {
        verdict.value for verdict in pr.EvalVerdict
    }


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
    # A legitimate-looking manifest edit (correct hash for a hypothetical
    # next version) still fails the attestation check because the digest moved.
    def bump(m):
        m["prompts"]["chat_system"]["version"] += 1

    path = _write_manifest_copy(tmp_path, bump)
    problems = pr.check_attestation(manifest_path=path)
    assert any("without a fresh eval-gate attestation" in p for p in problems)


def test_missing_attestation_fails(tmp_path):
    problems = pr.check_attestation(attestation_path=tmp_path / "missing.json")
    assert problems and "--attest" in problems[0]


def test_failed_verdict_fails(tmp_path):
    attestation = pr.load_attestation()
    attestation["verdict"] = "fail"
    path = tmp_path / "prompt_manifest_eval.json"
    path.write_text(json.dumps(attestation), encoding="utf-8")
    problems = pr.check_attestation(attestation_path=path)
    assert any("not pass" in p for p in problems)


# ── Attestation CLI behavior ────────────────────────────────────────────────


def test_offline_attest_writes_a_complete_passing_attestation(tmp_path, monkeypatch):
    attestation_path = tmp_path / "prompt_manifest_eval.json"
    manifest = {"prompts": {"prompt-a": {"version": 1, "content_hash": "abc123"}}}
    versions = {"prompt-a": "v1:abc123"}
    gate_results = [{"task_type": "classification", "status": "PASS"}]
    messages: list[str] = []

    monkeypatch.setattr(pr, "ATTESTATION_PATH", attestation_path)
    monkeypatch.setattr(pr, "check_manifest", lambda: [])
    monkeypatch.setattr(pr, "load_manifest", lambda: manifest)
    monkeypatch.setattr(pr, "registry_versions", lambda: versions)
    monkeypatch.setattr(pr, "_offline_gate_results", lambda: ("pass", gate_results))
    monkeypatch.setattr(pr, "_echo", messages.append)

    assert pr._attest("change-42", offline=True, notes="coverage slice") == 0

    payload = json.loads(attestation_path.read_text(encoding="utf-8"))
    assert payload["change_id"] == "change-42"
    assert payload["verdict"] == "pass"
    assert payload["mode"] == "offline_golden"
    assert payload["eval_gate_run_id"] is None
    assert payload["gate_results"] == gate_results
    assert payload["prompt_versions"] == versions
    assert payload["manifest_digest"] == pr.manifest_digest(manifest["prompts"])
    assert payload["notes"] == "coverage slice"
    assert messages and "verdict=pass" in messages[-1]


def test_offline_gate_never_passes_without_candidate_recordings():
    verdict, _ = pr._offline_gate_results()
    assert verdict is pr.EvalVerdict.INSUFFICIENT_SAMPLES


def test_attest_refuses_manifest_drift_without_overwriting_file(tmp_path, monkeypatch):
    attestation_path = tmp_path / "prompt_manifest_eval.json"
    attestation_path.write_text("existing-attestation\n", encoding="utf-8")
    messages: list[str] = []

    monkeypatch.setattr(pr, "ATTESTATION_PATH", attestation_path)
    monkeypatch.setattr(pr, "check_manifest", lambda: ["prompt-a hash drifted"])
    monkeypatch.setattr(pr, "_echo", messages.append)

    assert pr._attest("change-43", offline=True, notes="") == 1
    assert attestation_path.read_text(encoding="utf-8") == "existing-attestation\n"
    assert any("out of sync" in message for message in messages)
    assert any("prompt-a hash drifted" in message for message in messages)


def test_online_attest_failure_does_not_replace_existing_attestation(tmp_path, monkeypatch):
    attestation_path = tmp_path / "prompt_manifest_eval.json"
    attestation_path.write_text("existing-attestation\n", encoding="utf-8")
    messages: list[str] = []

    async def fail_gate(_change_id: str):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(pr, "ATTESTATION_PATH", attestation_path)
    monkeypatch.setattr(pr, "check_manifest", lambda: [])
    monkeypatch.setattr(pr, "_online_gate_result", fail_gate)
    monkeypatch.setattr(pr, "_echo", messages.append)

    assert pr._attest("change-44", offline=False, notes="") == 1
    assert attestation_path.read_text(encoding="utf-8") == "existing-attestation\n"
    assert any("retry with --offline" in message for message in messages)


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


def test_attestation_pins_model_routing_and_reviewer_sources():
    expected = {
        "backend/app/services/llm_factory.py",
        "backend/app/services/model_router.py",
        "backend/app/services/agent_capability_registry.py",
        "backend/app/agents/reviewer_agent.py",
    }
    assert set(pr.EVAL_ATTESTATION_WATCHED_PATHS) == expected
    assert pr.load_attestation()["watched_sources"] == pr.attestation_watched_sources()


def test_attestation_source_hash_is_independent_of_checkout_line_endings(tmp_path):
    relative = pr.EVAL_ATTESTATION_WATCHED_PATHS[0]
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"first\nsecond\n")
    lf_hash = pr.attestation_watched_sources(tmp_path)[relative]
    target.write_bytes(b"first\r\nsecond\r\n")

    assert pr.attestation_watched_sources(tmp_path)[relative] == lf_hash


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
