"""Quality-gate prompt changes on scored model outputs (re-audit M16).

The ``ai.prompt-manifest-sync`` guard forces a version bump and an eval-gate
attestation for every prompt edit -- but the offline attestation scores the
golden datasets, whose ``expected_output`` already records whether each answer
was correct. Nothing in it depends on the prompt text: an edit that made the
classifier answer "UNKNOWN" to everything would attest exactly as green as
the prompt it replaced. The gate checked that a version number moved, not that
the outputs stayed good.

This module gates the prompts whose output is a decision that can be scored:

* ``prompt_eval_recordings.json`` holds, per gated prompt, golden cases, the
  RAW model outputs recorded for them, and the prompt ``content_hash`` the
  outputs were produced under;
* :func:`check_recordings` fails when a gated prompt's current hash differs
  from the hash its outputs were recorded under (the prompt changed and was
  not re-run), when an entry claims to be measured but has no outputs, or when
  the outputs score below the entry's ``min_score``;
* ``python -m app.services.prompt_eval_recordings --record <prompt_id>`` runs
  the CURRENT prompt through ``llm_factory.get_llm`` on every case, stores the
  outputs and the hash, and scores them.

Honest about what is not measured yet. Real outputs need a model, and none
were recorded when this landed, so the three gated prompts start *unmeasured*
-- listed by name by :func:`check_recordings`, never counted as passing. An
unmeasured entry is accepted only at the exact hash frozen in
:data:`GRANDFATHERED_UNMEASURED`: the first edit to one of these prompts
fails the gate until its outputs are recorded and scored. Moving the frozen
hash instead is an explicit, reviewable code change.

The check runs as a backend test (tests/test_prompt_eval_recordings.py),
which CI's backend job executes, and as ``--check`` for a dedicated CI step.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.services.prompt_registry import get_prompt

RECORDINGS_PATH = Path(__file__).resolve().parent / "prompt_eval_recordings.json"

#: prompt id -> scoring task. Only prompts whose output is a checkable decision.
GATED_PROMPTS: dict[str, str] = {
    "fast_classifier_system": "classification",
    "regression_watchman_classify": "regression_classification",
    "release_risk_reasoning": "release_grounding",
}

#: The only (prompt id, hash) pairs allowed to be unmeasured. Shrinks to empty
#: as prompts are recorded; never add to it to get past the gate.
GRANDFATHERED_UNMEASURED: dict[str, str] = {
    "fast_classifier_system": "9d46ffbbed7b",
    "regression_watchman_classify": "2ac1510063fd",
    "release_risk_reasoning": "a152fb254091",
}

_CATEGORIES = {"PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY", "UNKNOWN"}


# ── scoring ─────────────────────────────────────────────────────────────────


def _json_object(text: str) -> Optional[dict]:
    """The first JSON object in a model answer (code fences tolerated)."""
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _score_classification(case: dict) -> float:
    parsed = _json_object(case.get("output", ""))
    if not parsed:
        return 0.0
    category = str(parsed.get("category", "")).upper()
    return 1.0 if category in _CATEGORIES and category == case["expected"]["category"] else 0.0


def _score_regression_classification(case: dict) -> float:
    parsed = _json_object(case.get("output", ""))
    expected: dict = case["expected"]["classifications"]
    if not parsed or not expected:
        return 0.0
    hits = sum(
        1 for cluster_id, label in expected.items()
        if isinstance(parsed.get(cluster_id), dict)
        and parsed[cluster_id].get("classification") == label
    )
    return hits / len(expected)


def _score_release_grounding(case: dict) -> float:
    """The prompt's own grounding rules, checked on the answer."""
    parsed = _json_object(case.get("output", ""))
    if not parsed or not isinstance(parsed.get("reasoning"), str) or not parsed["reasoning"].strip():
        return 0.0
    blocking = parsed.get("blocking_issues")
    conditions = parsed.get("conditions_for_go")
    if not isinstance(blocking, list) or not isinstance(conditions, list):
        return 0.0
    expected = case["expected"]
    if not expected["has_failures"] and blocking:
        return 0.0  # issues invented with no failure data
    if expected["recommendation"] != "CONDITIONAL_GO" and conditions:
        return 0.0  # conditions only belong to CONDITIONAL_GO
    return 1.0


SCORERS: dict[str, Callable[[dict], float]] = {
    "classification": _score_classification,
    "regression_classification": _score_regression_classification,
    "release_grounding": _score_release_grounding,
}


def score_entry(entry: dict) -> float:
    cases = entry.get("cases") or []
    if not cases:
        return 0.0
    scorer = SCORERS[entry["task_type"]]
    return round(sum(scorer(case) for case in cases) / len(cases), 4)


# ── the check ───────────────────────────────────────────────────────────────


def load_recordings(path: Optional[Path] = None) -> dict[str, Any]:
    return dict(json.loads((path or RECORDINGS_PATH).read_text(encoding="utf-8")))


def check_recordings(path: Optional[Path] = None) -> tuple[list[str], list[str]]:
    """``(problems, unmeasured)``. Empty problems = the gate passes."""
    try:
        prompts = load_recordings(path).get("prompts")
    except (OSError, ValueError) as exc:
        return [f"prompt_eval_recordings.json unreadable: {exc}"], []
    if not isinstance(prompts, dict):
        return ["prompt_eval_recordings.json has no 'prompts' map"], []

    problems: list[str] = []
    unmeasured: list[str] = []
    for prompt_id, task_type in sorted(GATED_PROMPTS.items()):
        current = get_prompt(prompt_id).content_hash
        entry = prompts.get(prompt_id)
        if not isinstance(entry, dict):
            problems.append(f"{prompt_id}: no recorded outputs")
            continue
        if entry.get("task_type") != task_type:
            problems.append(f"{prompt_id}: task_type {entry.get('task_type')!r} != {task_type!r}")
            continue
        recorded_hash = entry.get("content_hash")
        if not entry.get("measured"):
            if GRANDFATHERED_UNMEASURED.get(prompt_id) == current == recorded_hash:
                unmeasured.append(prompt_id)
            else:
                problems.append(
                    f"{prompt_id}: prompt hash is {current} and it has no scored outputs -- "
                    f"record them: python -m app.services.prompt_eval_recordings --record {prompt_id}"
                )
            continue
        if recorded_hash != current:
            problems.append(
                f"{prompt_id}: prompt changed (hash {current}) since its outputs were recorded "
                f"(under {recorded_hash}) -- re-record: "
                f"python -m app.services.prompt_eval_recordings --record {prompt_id}"
            )
            continue
        cases = entry.get("cases") or []
        if not cases or any(not isinstance(c.get("output"), str) for c in cases):
            problems.append(f"{prompt_id}: marked measured but a case has no recorded output")
            continue
        score = score_entry(entry)
        minimum = float(entry.get("min_score", 1.0))
        if score < minimum:
            problems.append(f"{prompt_id}: recorded outputs score {score:.2f} < min_score {minimum:.2f}")
    for prompt_id in sorted(set(prompts) - set(GATED_PROMPTS)):
        problems.append(f"{prompt_id}: recordings entry for a prompt that is not gated (stale)")
    return problems, unmeasured


# ── recording ───────────────────────────────────────────────────────────────


def render_messages(prompt_id: str, case_input: dict) -> list:
    """The messages the production call site sends for this prompt."""
    from langchain_core.messages import HumanMessage, SystemMessage

    text = get_prompt(prompt_id).text
    if prompt_id == "fast_classifier_system":
        # Mirrors training/classifier.FastClassifier.classify_with_outcome.
        stack = case_input.get("stack_trace") or ""
        user = (
            f"Test: {case_input['test_name']}\n"
            f"Error: {case_input['error_message'][:1500]}\n"
            + (f"Stack (first 500 chars): {stack[:500]}" if stack else "")
        ).strip()
        return [SystemMessage(content=text), HumanMessage(content=user)]
    return [HumanMessage(content=text.format(**case_input))]


async def record(
    prompt_id: str,
    *,
    invoke: Callable[[list], Awaitable[Any]],
    path: Optional[Path] = None,
    recorded_by: str = "",
) -> dict:
    """Run the CURRENT prompt on every case, store raw outputs + hash + score."""
    data = load_recordings(path)
    entry = dict(data["prompts"][prompt_id])
    cases = []
    for case in entry["cases"]:
        response = await invoke(render_messages(prompt_id, case["input"]))
        content = getattr(response, "content", response)
        cases.append({**case, "output": content if isinstance(content, str) else str(content)})
    entry.update(
        cases=cases,
        content_hash=get_prompt(prompt_id).content_hash,
        measured=True,
        recorded_by=recorded_by,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    entry["score"] = score_entry(entry)
    data["prompts"][prompt_id] = entry
    (path or RECORDINGS_PATH).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
    )
    return entry


async def _record_with_llm(prompt_id: str, model: Optional[str]) -> dict:
    from app.services.llm_factory import get_llm

    llm = await get_llm(model=model, temperature=0.0)
    return await record(prompt_id, invoke=llm.ainvoke, recorded_by=f"{getattr(llm, '_provider', '?')}:{model or ''}")


def _echo(message: str) -> None:
    """CLI output. Plain stdout on purpose -- developer tooling, not app
    runtime (the backend.no-print gate keeps print() out of backend/app)."""
    sys.stdout.write(message + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scored-output gate for prompt changes (M16).")
    parser.add_argument("--check", action="store_true", help="Fail when a gated prompt lacks current scored outputs.")
    parser.add_argument("--record", metavar="PROMPT_ID", help="Record outputs for one gated prompt with the configured LLM.")
    parser.add_argument("--model", help="With --record: model override.")
    args = parser.parse_args(argv)
    if args.record:
        if args.record not in GATED_PROMPTS:
            _echo(f"{args.record} is not a gated prompt: {sorted(GATED_PROMPTS)}")
            return 2
        entry = asyncio.run(_record_with_llm(args.record, args.model))
        _echo(f"recorded {len(entry['cases'])} case(s) for {args.record}: score {entry['score']:.2f}")
        return 0 if entry["score"] >= float(entry.get("min_score", 1.0)) else 1
    problems, unmeasured = check_recordings()
    for problem in problems:
        _echo(f"FAIL {problem}")
    for prompt_id in unmeasured:
        _echo(f"UNMEASURED {prompt_id} (grandfathered at its current hash; its next edit needs recorded outputs)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
