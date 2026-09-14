"""Evidence for deterministic classifications (F-17).

The problem
-----------
``benchmarks/pipeline/collect.py`` measured, on the homelab 2026-08-22:

    4,693 analyses, 3 carrying any evidence_references  (0.06%)
    rules 4,087 / llm 603

F-3 gave the narrative a citation contract, and it measured 0 of 30 cited --
not because the model declined it, but because ``build_evidence_catalogue``
had nothing to put in front of the model. ``rules_engine._build_result``
hardcodes ``"evidence_references": []``, and the rules engine produces 87% of
all analyses. The citation mechanism was fixed at the wrong end of the chain.

What counts as evidence for a rule
----------------------------------
A deterministic classifier has no tool observations, but it is not evidence-free:
**the input it matched on is its evidence.** "INFRASTRUCTURE, because this error
text contains 'connection refused'" is a checkable claim, and the text is what
makes it checkable.

Two things this deliberately does NOT do:

* **It never cites the classifier's own conclusion.** ``root_cause_summary`` is
  the output; quoting it back as support would be circular, and would inflate
  the coverage metric while teaching a reader nothing.
* **It never claims to be an authorized artifact.** Tool observations carry an
  HMAC attestation and are captured into the signed evidence store
  (``evidence_artifact_service``). Classifier inputs are local provenance:
  ``kind`` is ``classifier_input``, which that service skips rather than
  attests. Blurring the two would weaken an authority boundary that exists to
  stop model-supplied text becoming signed evidence.

Pure and total: no database, no LLM, no outbound calls, and no input shape
raises -- a malformed analysis loses its evidence, never the classification.
"""
from __future__ import annotations

from typing import Any, Optional

# Marks a reference as classifier provenance rather than an attested tool
# observation. ``evidence_artifact_service`` skips this kind on purpose.
CLASSIFIER_EVIDENCE_KIND = "classifier_input"

# Engines whose classification is a deterministic function of their input, so
# the input is a complete and honest account of why the verdict was reached.
_DETERMINISTIC_ENGINES = {"rules_engine", "ml_classifier", "rules", "ml"}

# The fast classifier is an LLM call, but a single-shot one that runs NO tools:
# it classifies purely from the error text handed to it and returns before the
# ReAct loop that would gather observations. That makes its evidence story
# identical to a rule's -- the input it matched on is what it has -- and it is
# why excluding "llm" wholesale left this deployment at 0.06% coverage after
# F-17 shipped. Measured: all 593 LLM analyses carried tools_used = [].
_TOOL_FREE_EXECUTION_PATHS = {"fast_classifier", "tiered_root_cause"}

_EXCERPT_LIMIT = 300


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = _EXCERPT_LIMIT) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _sanitize(value: str) -> str:
    """Strip secrets/PII before the excerpt crosses a persistence boundary."""
    try:
        from app.services.evidence_sanitizer import sanitize_reference_text

        cleaned, _, _ = sanitize_reference_text(value, limit=_EXCERPT_LIMIT)
        return cleaned
    except Exception:  # noqa: BLE001 — never lose a classification to redaction
        return value[:_EXCERPT_LIMIT]


def _engine_of(analysis: dict) -> Optional[str]:
    for key in ("classified_by", "llm_model"):
        value = str(analysis.get(key) or "").strip().lower()
        if value in _DETERMINISTIC_ENGINES:
            return "rules_engine" if value in {"rules_engine", "rules"} else "ml_classifier"

    # A tool-free LLM verdict. Not "deterministic", but evidentially in the same
    # position: it saw only the error text, so that text is the whole of what
    # supports it. Checked via the routing record rather than the model name so
    # a provider change cannot silently reopen the gap.
    routing = _as_dict(analysis.get("_routing"))
    if routing.get("execution_path") in _TOOL_FREE_EXECUTION_PATHS:
        return "fast_classifier"

    # A ReAct verdict that ran tools but recorded nothing is NOT covered here:
    # its evidence should come from the tools, and papering over their absence
    # would hide a real failure behind a synthetic citation.
    return None


def build_classifier_evidence(
    analysis: Any, test_case: Any, history: Any = None
) -> list[dict[str, Any]]:
    """Evidence for a deterministic verdict, or ``[]`` when there is none.

    Returns at most one reference. Order of preference:

    1. the **error text** the pattern rules matched on;
    2. the **historical record** a flakiness heuristic counted, when there is no
       error text -- the counts are the thing that fired the rule.

    When neither exists the answer is ``[]``. A classifier that reached a
    verdict from nothing citable should report nothing citable, rather than
    manufacture a reference to make a coverage metric look better.
    """
    analysis = _as_dict(analysis)
    test_case = _as_dict(test_case)
    history = _as_dict(history)

    engine = _engine_of(analysis)
    if engine is None:
        return []
    # Never overwrite real tool observations -- an LLM path that gathered
    # evidence has strictly better references than this.
    if analysis.get("evidence_references"):
        return []

    rule_id = _text(analysis.get("confidence_rule_id"), 120) or None
    test_name = _text(test_case.get("test_name"), 200) or None

    error_text = _text(test_case.get("error_message"))
    if error_text:
        return [{
            "source": engine,
            "kind": CLASSIFIER_EVIDENCE_KIND,
            "excerpt": _sanitize(error_text),
            "rule_id": rule_id,
            "test_name": test_name,
            "basis": "matched_error_text",
        }]

    passed = history.get("pass_count")
    failed = history.get("fail_count")
    if isinstance(passed, int) and isinstance(failed, int) and (passed + failed) > 0:
        total = passed + failed
        return [{
            "source": engine,
            "kind": CLASSIFIER_EVIDENCE_KIND,
            "excerpt": f"{failed} of {total} historical runs failed for this test.",
            "rule_id": rule_id,
            "test_name": test_name,
            "basis": "historical_record",
        }]

    return []


def attach_classifier_evidence(
    analysis: Any, test_case: Any, history: Any = None
) -> Any:
    """Attach classifier evidence in place when the verdict carries none.

    Returns the analysis unchanged for LLM verdicts, for analyses that already
    carry references, and for any input shape that is not a dict.
    """
    if not isinstance(analysis, dict):
        return analysis
    evidence = build_classifier_evidence(analysis, test_case, history)
    if evidence:
        analysis["evidence_references"] = evidence
    return analysis


def applies_for_authorization(reference: Any) -> bool:
    """Whether an evidence reference is a candidate for artifact authorization.

    Classifier provenance never applies. It records the error text a local
    rule matched on -- not an attested tool observation -- so the capture loop
    skips it without recording an authorization error.

    The bundle's silent-loss guard must use the SAME definition. When the two
    disagreed, every run whose failures were all fast-classified produced
    candidate references, zero authorized artifacts and zero errors -- which
    reads as evidence vanishing silently. The guard fired, the critic's
    ``metric_data_quality`` check failed, and the decision report never
    published.

    A malformed reference *is* a candidate: it should be counted and reported
    as invalid by the capture loop, not quietly dropped here.
    """
    if not isinstance(reference, dict):
        return True
    kind = str(reference.get("kind") or "").strip()
    return kind != CLASSIFIER_EVIDENCE_KIND
