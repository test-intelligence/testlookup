"""Re-audit M16: a prompt change needs scored outputs, not only a version bump.

The existing attestation scores golden items whose correctness is written in
the dataset, so it passes whatever the prompt says. This gate compares each
gated prompt's CURRENT hash with the hash its recorded outputs were produced
under, and scores those outputs. The tree test below is the check CI runs.
"""
from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from app.services import prompt_eval_recordings as rec
from app.services import prompt_registry
from app.services.prompt_registry import PromptDef

CLASSIFIER = "fast_classifier_system"


def test_the_tree_passes_and_names_every_unmeasured_prompt():
    problems, unmeasured = rec.check_recordings()
    assert problems == []
    # Honest: no real outputs have been recorded yet, and the gate says so.
    assert unmeasured == sorted(rec.GATED_PROMPTS)
    assert rec.main(["--check"]) == 0


def test_every_case_renders_through_its_prompt():
    for prompt_id, entry in rec.load_recordings()["prompts"].items():
        for case in entry["cases"]:
            messages = rec.render_messages(prompt_id, case["input"])
            assert messages and all(m.content for m in messages)


def _edit(monkeypatch, prompt_id: str, suffix: str = "\nBe brief.") -> str:
    old = prompt_registry._PROMPTS[prompt_id]
    new = PromptDef(id=prompt_id, version=old.version + 1, text=old.text + suffix)
    monkeypatch.setitem(prompt_registry._PROMPTS, prompt_id, new)
    return new.content_hash


def _copy(tmp_path):
    path = tmp_path / "recordings.json"
    shutil.copy(rec.RECORDINGS_PATH, path)
    return path


def test_editing_a_gated_prompt_without_recording_outputs_fails(monkeypatch):
    _edit(monkeypatch, CLASSIFIER)
    problems, unmeasured = rec.check_recordings()
    assert any(p.startswith(f"{CLASSIFIER}:") and "no scored outputs" in p for p in problems)
    assert CLASSIFIER not in unmeasured


def test_the_unmeasured_escape_cannot_be_moved_by_editing_the_json(tmp_path, monkeypatch):
    new_hash = _edit(monkeypatch, CLASSIFIER)
    path = _copy(tmp_path)
    data = json.loads(path.read_text())
    data["prompts"][CLASSIFIER]["content_hash"] = new_hash  # "re-pin" without recording
    path.write_text(json.dumps(data))
    problems, _ = rec.check_recordings(path)
    assert any(p.startswith(f"{CLASSIFIER}:") for p in problems)


class _Model:
    def __init__(self, answer):
        self.answer = answer
        self.seen: list = []

    async def __call__(self, messages):
        self.seen.append(messages)
        return SimpleNamespace(content=self.answer(messages))


def _classifier_answer(path, *, wrong=False):
    cases = json.loads(path.read_text())["prompts"][CLASSIFIER]["cases"]
    truth = {c["input"]["test_name"]: c["expected"]["category"] for c in cases}

    def answer(messages):
        name = messages[-1].content.splitlines()[0].removeprefix("Test: ")
        category = "UNKNOWN" if wrong else truth[name]
        return f'```json\n{{"category": "{category}", "confidence": 90, "reasoning": "r"}}\n```'

    return answer


@pytest.mark.asyncio
async def test_a_changed_prompt_recorded_with_good_outputs_passes(tmp_path, monkeypatch):
    new_hash = _edit(monkeypatch, CLASSIFIER)
    path = _copy(tmp_path)
    model = _Model(_classifier_answer(path))
    entry = await rec.record(CLASSIFIER, invoke=model, path=path, recorded_by="fake:test")
    assert entry["measured"] is True
    assert entry["content_hash"] == new_hash
    assert entry["score"] == 1.0
    # The model was shown the NEW prompt text, not the old one.
    assert model.seen[0][0].content.endswith("Be brief.")
    problems, unmeasured = rec.check_recordings(path)
    assert problems == []
    assert CLASSIFIER not in unmeasured


@pytest.mark.asyncio
async def test_recorded_outputs_that_score_badly_fail(tmp_path, monkeypatch):
    _edit(monkeypatch, CLASSIFIER)
    path = _copy(tmp_path)
    entry = await rec.record(CLASSIFIER, invoke=_Model(_classifier_answer(path, wrong=True)), path=path)
    assert entry["score"] == pytest.approx(0.2)  # only the UNKNOWN case is right
    problems, _ = rec.check_recordings(path)
    assert any("score 0.20 < min_score 0.80" in p for p in problems)


@pytest.mark.asyncio
async def test_outputs_recorded_under_an_older_prompt_fail(tmp_path, monkeypatch):
    _edit(monkeypatch, CLASSIFIER)
    path = _copy(tmp_path)
    await rec.record(CLASSIFIER, invoke=_Model(_classifier_answer(path)), path=path)
    _edit(monkeypatch, CLASSIFIER, suffix="\nBe very brief.")  # edited again, not re-recorded
    problems, _ = rec.check_recordings(path)
    assert any("changed" in p and "re-record" in p for p in problems)


def test_a_measured_entry_without_outputs_fails(tmp_path):
    path = _copy(tmp_path)
    data = json.loads(path.read_text())
    data["prompts"][CLASSIFIER]["measured"] = True
    path.write_text(json.dumps(data))
    problems, _ = rec.check_recordings(path)
    assert any("no recorded output" in p for p in problems)


def test_missing_and_stale_entries_fail(tmp_path):
    path = _copy(tmp_path)
    data = json.loads(path.read_text())
    del data["prompts"]["release_risk_reasoning"]
    data["prompts"]["chat_system"] = {"task_type": "classification", "cases": []}
    path.write_text(json.dumps(data))
    problems, _ = rec.check_recordings(path)
    assert "release_risk_reasoning: no recorded outputs" in problems
    assert any(p.startswith("chat_system:") and "stale" in p for p in problems)


# ── the scorers read the answer ─────────────────────────────────────────────


def test_classification_scorer():
    case = {"expected": {"category": "FLAKY"}}
    assert rec._score_classification({**case, "output": '{"category": "FLAKY"}'}) == 1.0
    assert rec._score_classification({**case, "output": '{"category": "PRODUCT_BUG"}'}) == 0.0
    assert rec._score_classification({**case, "output": "FLAKY"}) == 0.0  # not JSON


def test_regression_scorer_gives_partial_credit():
    case = {
        "expected": {"classifications": {"a": "new_regression", "b": "environmental_anomaly"}},
        "output": '{"a": {"classification": "new_regression"}, "b": {"classification": "new_regression"}}',
    }
    assert rec._score_regression_classification(case) == 0.5


def test_release_grounding_scorer_applies_the_prompts_rules():
    ok = '{"reasoning": "No failures detected.", "blocking_issues": [], "conditions_for_go": []}'
    invented = '{"reasoning": "r", "blocking_issues": ["made up"], "conditions_for_go": []}'
    conditions_on_go = '{"reasoning": "r", "blocking_issues": [], "conditions_for_go": ["x"]}'
    go = {"expected": {"has_failures": False, "recommendation": "GO"}}
    assert rec._score_release_grounding({**go, "output": ok}) == 1.0
    assert rec._score_release_grounding({**go, "output": invented}) == 0.0
    assert rec._score_release_grounding({**go, "output": conditions_on_go}) == 0.0
    conditional = {"expected": {"has_failures": True, "recommendation": "CONDITIONAL_GO"}}
    assert rec._score_release_grounding({**conditional, "output": conditions_on_go}) == 1.0
