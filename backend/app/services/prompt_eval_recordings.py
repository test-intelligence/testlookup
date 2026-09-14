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

Recordings carry provenance (QA-B45-A3): ``--record`` writes the provider,
the model, when, and ``outputs_sha256`` -- a digest over everything the
verdict depends on: the prompt hash, ``min_score``, and every case's id,
input, expected answer and output -- and :func:`check_recordings` refuses a
measured entry without them, or whose recording no longer matches the digest
(an output, an expected answer, an input or the pass bar edited by hand after
recording, R-B45-R2-3). This is NOT a cryptographic binding: the
repository holds no signing key, so someone determined can recompute the
digest. What it does is make a hand-written or hand-edited recording a
deliberate act, visible in the PR diff of ``prompt_eval_recordings.json``,
where recordings are reviewed like code.

Honest about what is not measured yet. Real outputs need a model, and none
were recorded when this landed, so the gated prompts start *unmeasured*
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
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.services.eval_verdict import EvalVerdict
from app.services.agent_eval_harness import prose_contract_text
from app.services.prompt_registry import get_prompt

RECORDINGS_PATH = Path(__file__).resolve().parent / "prompt_eval_recordings.json"

#: prompt id -> scoring task. Only prompts whose output is a checkable decision.
GATED_PROMPTS: dict[str, str] = {
    "anomaly_narrative": "prose_rubric",
    "fast_classifier_system": "classification",
    "investigator_synthesis_narrative": "prose_rubric",
    "regression_watchman_classify": "regression_classification",
    "release_risk_reasoning": "release_grounding",
}

#: The only (prompt id, hash) pairs allowed to be unmeasured. Shrinks to empty
#: as prompts are recorded; never add to it to get past the gate.
GRANDFATHERED_UNMEASURED: dict[str, str] = {
    "anomaly_narrative": "0298a3b5009f",
    "fast_classifier_system": "9d46ffbbed7b",
    "investigator_synthesis_narrative": "f7d92dd09981",
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
    """The decision, then the prompt's own grounding rules, on the answer.

    The recommendation is the decision the gate exists for: a model that
    answers "GO, no issues" to everything used to score 1.0 (QA-B45-A2).
    """
    parsed = _json_object(case.get("output", ""))
    if not parsed or not isinstance(parsed.get("reasoning"), str) or not parsed["reasoning"].strip():
        return 0.0
    blocking = parsed.get("blocking_issues")
    conditions = parsed.get("conditions_for_go")
    if not isinstance(blocking, list) or not isinstance(conditions, list):
        return 0.0
    expected = case["expected"]
    if str(parsed.get("recommendation", "")).strip().upper() != expected["recommendation"]:
        return 0.0  # the wrong decision
    if not expected["has_failures"] and blocking:
        return 0.0  # issues invented with no failure data
    if expected["recommendation"] == "NO_GO" and expected["has_failures"] and not blocking:
        return 0.0  # a NO_GO must name what blocks it
    if expected["recommendation"] == "CONDITIONAL_GO" and not conditions:
        return 0.0  # a CONDITIONAL_GO must say what the conditions are
    if expected["recommendation"] != "CONDITIONAL_GO" and conditions:
        return 0.0  # conditions only belong to CONDITIONAL_GO
    return 1.0


def _score_prose_rubric(case: dict) -> float:
    """Score prose for parseability and the case's explicit grounding rubric."""
    rubric = case.get("expected") or {}
    output = prose_contract_text(
        case.get("output"), json_field=rubric.get("json_field"),
    )
    if not output:
        return 0.0
    lowered = output.lower()
    terms = [str(term).lower() for term in rubric.get("required_terms", [])]
    if any(term not in lowered for term in terms):
        return 0.0
    sentences = [part for part in re.split(r"[.!?]+", output) if part.strip()]
    minimum = int(rubric.get("min_sentences", 1))
    maximum = int(rubric.get("max_sentences", 100))
    if not minimum <= len(sentences) <= maximum:
        return 0.0
    if rubric.get("requires_action") and not any(
        token in lowered for token in ("investigate", "fix", "retry", "check", "restore")
    ):
        return 0.0
    return 1.0


SCORERS: dict[str, Callable[[dict], float]] = {
    "classification": _score_classification,
    "regression_classification": _score_regression_classification,
    "release_grounding": _score_release_grounding,
    "prose_rubric": _score_prose_rubric,
}


def score_entry(entry: dict) -> float:
    cases = entry.get("cases") or []
    if not cases:
        return 0.0
    scorer = SCORERS[entry["task_type"]]
    return round(sum(scorer(case) for case in cases) / len(cases), 4)


# ── provenance ──────────────────────────────────────────────────────────────


def outputs_digest(content_hash: str, cases: list[dict], min_score: Any) -> str:
    """sha256 over everything the verdict depends on, in canonical JSON: the
    prompt hash, the pass bar (``min_score``, as written: adding or removing
    the key changes it too) and every case's id, input, expected answer and
    output, in order.

    Hashing only the outputs (R-B45-R2-3) let ``expected`` or ``min_score``
    be edited after recording -- flip a case's expected answer to what the
    model said, or lower the bar -- while the digest still verified.
    """
    payload = json.dumps(
        {
            "content_hash": content_hash,
            "min_score": min_score,
            "cases": [
                [c.get("case_id"), c.get("input"), c.get("expected"), c.get("output")] for c in cases
            ],
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provenance_problem(prompt_id: str, entry: dict) -> Optional[str]:
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict) or not all(
        isinstance(provenance.get(k), str) and provenance[k].strip()
        for k in ("provider", "model", "recorded_at", "outputs_sha256")
    ):
        return (
            f"{prompt_id}: recorded outputs have no provenance (provider, model, recorded_at, "
            f"outputs_sha256) -- record them with the model: "
            f"python -m app.services.prompt_eval_recordings --record {prompt_id}"
        )
    expected_digest = outputs_digest(
        str(entry.get("content_hash")), entry.get("cases") or [], entry.get("min_score"),
    )
    if provenance["outputs_sha256"] != expected_digest:
        return (
            f"{prompt_id}: the recording (outputs, cases or min_score) does not match its "
            f"provenance digest (edited after recording?)"
        )
    return None


def _keyed_recordings_problem(prompt_id: str, entry: dict) -> Optional[str]:
    recordings = entry.get("recordings")
    if not isinstance(recordings, dict):
        return f"{prompt_id}: recordings must be keyed by content_hash then provider/model@tier"
    for content_hash, by_model in recordings.items():
        if not isinstance(by_model, dict):
            return f"{prompt_id}: recordings[{content_hash!r}] is not a model map"
        for model_ref, recording in by_model.items():
            if not isinstance(recording, dict) or "/" not in model_ref or "@" not in model_ref:
                return f"{prompt_id}: invalid recording key {content_hash!r}, {model_ref!r}"
            provenance = recording.get("provenance") or {}
            expected_ref = (
                f"{provenance.get('provider', '')}/{provenance.get('model', '')}"
                f"@{provenance.get('tier', '')}"
            )
            if model_ref != expected_ref:
                return f"{prompt_id}: recording key {model_ref!r} disagrees with provenance"
            candidate = {
                "content_hash": content_hash,
                "cases": recording.get("cases"),
                "min_score": entry.get("min_score"),
                "provenance": provenance,
            }
            problem = _provenance_problem(prompt_id, candidate)
            if problem:
                return problem
            score = score_entry({**entry, "cases": recording.get("cases") or []})
            minimum = float(entry.get("min_score", 1.0))
            if score < minimum:
                return (
                    f"{prompt_id}: keyed recording {model_ref} score {score:.2f} "
                    f"< min_score {minimum:.2f}"
                )
    return None


# ── the check ───────────────────────────────────────────────────────────────


def load_recordings(path: Optional[Path] = None) -> dict[str, Any]:
    return dict(json.loads((path or RECORDINGS_PATH).read_text(encoding="utf-8")))


def recording_key(content_hash: str, provider: str, model: str, tier: str) -> tuple[str, str]:
    """Stable identity required to compare the same prompt across model tiers."""
    return content_hash, f"{provider}/{model}@{tier}"


def recordings_verdict(problems: list[str], insufficient: list[str]) -> EvalVerdict:
    if problems:
        return EvalVerdict.FAIL
    if insufficient:
        return EvalVerdict.INSUFFICIENT_SAMPLES
    return EvalVerdict.PASS


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
        keyed_problem = _keyed_recordings_problem(prompt_id, entry)
        if keyed_problem:
            problems.append(keyed_problem)
            continue
        cases = entry.get("cases") or []
        if not cases or any(not isinstance(c.get("output"), str) for c in cases):
            problems.append(f"{prompt_id}: marked measured but a case has no recorded output")
            continue
        provenance_problem = _provenance_problem(prompt_id, entry)
        if provenance_problem:
            problems.append(provenance_problem)
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
    provider: str = "",
    model: str = "",
    tier: str = "llm",
) -> dict:
    """Run the CURRENT prompt on every case, store raw outputs + hash + score,
    and the provenance :func:`check_recordings` requires."""
    data = load_recordings(path)
    entry = dict(data["prompts"][prompt_id])
    cases = []
    for case in entry["cases"]:
        response = await invoke(render_messages(prompt_id, case["input"]))
        content = getattr(response, "content", response)
        cases.append({**case, "output": content if isinstance(content, str) else str(content)})
    content_hash = get_prompt(prompt_id).content_hash
    recorded_at = datetime.now(timezone.utc).isoformat()
    entry.update(
        cases=cases,
        content_hash=content_hash,
        measured=True,
        recorded_by=recorded_by,
        recorded_at=recorded_at,
        provenance={
            "provider": provider,
            "model": model,
            "recorded_at": recorded_at,
            "outputs_sha256": outputs_digest(content_hash, cases, entry.get("min_score")),
        },
    )
    entry["score"] = score_entry(entry)
    content_key, model_key = recording_key(content_hash, provider, model, tier)
    recordings = dict(entry.get("recordings") or {})
    by_model = dict(recordings.get(content_key) or {})
    by_model[model_key] = {
        "cases": cases,
        "score": entry["score"],
        "provenance": {**entry["provenance"], "tier": tier},
    }
    recordings[content_key] = by_model
    entry["recordings"] = recordings
    data["prompts"][prompt_id] = entry
    (path or RECORDINGS_PATH).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n",
    )
    return entry


async def _record_with_llm(prompt_id: str, model: Optional[str], tier: str) -> dict:
    from app.services.llm_factory import get_llm

    llm = await get_llm(model=model, temperature=0.0)
    provider = str(getattr(llm, "_provider", "") or "")
    model_name = str(getattr(llm, "_model", "") or model or "")
    return await record(
        prompt_id, invoke=llm.ainvoke, recorded_by=f"{provider}:{model_name}",
        provider=provider, model=model_name, tier=tier,
    )


def _echo(message: str) -> None:
    """CLI output. Plain stdout on purpose -- developer tooling, not app
    runtime (the backend.no-print gate keeps print() out of backend/app)."""
    sys.stdout.write(message + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scored-output gate for prompt changes (M16).")
    parser.add_argument("--check", action="store_true", help="Fail when a gated prompt lacks current scored outputs.")
    parser.add_argument("--record", metavar="PROMPT_ID", help="Record outputs for one gated prompt with the configured LLM.")
    parser.add_argument("--model", help="With --record: model override.")
    parser.add_argument(
        "--tier", choices=("slm", "llm"), default="llm",
        help="With --record: tier represented by the selected model.",
    )
    args = parser.parse_args(argv)
    if args.record:
        if args.record not in GATED_PROMPTS:
            _echo(f"{args.record} is not a gated prompt: {sorted(GATED_PROMPTS)}")
            return 2
        entry = asyncio.run(_record_with_llm(args.record, args.model, args.tier))
        _echo(f"recorded {len(entry['cases'])} case(s) for {args.record}: score {entry['score']:.2f}")
        return 0 if entry["score"] >= float(entry.get("min_score", 1.0)) else 1
    problems, unmeasured = check_recordings()
    for problem in problems:
        _echo(f"FAIL {problem}")
    for prompt_id in unmeasured:
        _echo(
            f"INSUFFICIENT_SAMPLES {prompt_id} (grandfathered at its current hash; "
            "its next edit needs recorded outputs)"
        )
    # Grandfathered insufficient evidence is reported honestly but does not
    # make every unchanged branch red. A prompt edit removes that grandfather
    # and becomes a problem, so candidate changes still fail closed.
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
