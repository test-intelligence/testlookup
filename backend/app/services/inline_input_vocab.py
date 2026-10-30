"""The inline agent-input vocabulary (W23c AGT-11), in ONE place.

``POST /api/v1/agents/{agent_id}/invoke`` accepts an ``inline_input`` for the
agents in ``INLINE_INPUT_AGENTS``. The input is attacker-controlled (an API key
or a browser): it is size-capped (413), validated against the closed
``models.inline_agent_input.InlineSummaryInputV1`` (422), redacted before it is
stored or hashed, and reaches the model ONLY as delimited data blocks
(``services/untrusted_input.wrap_untrusted``). It must name a stored test run
of the project (the D18 anchor); every wrong anchor answers ONE 404
``anchor_run_not_found`` (no existence oracle). Its output is a draft kept out
of the run's report, release gates, decisions, notifications and digests.

The frontend mirrors these in ``frontend/src/types/agentInvoke.ts``;
``tests/test_review_gate_inline_vocab_parity.py`` pins the mirror and the
downstream literals ``WRAP_LIMITS`` must equal.

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

INLINE_INPUT_AGENTS = ("agent.summary.v1",)       # the only agent with an inline adapter in W23c; others → 422
INLINE_INPUT_MAX_BYTES = 262_144                  # canonical compact JSON of `inline_input`, UTF-8, BEFORE scrubbing → 413
INLINE_FAILURES_MAX = 10                          # failures per payload (keeps the worst-case context inside the default token budget)
TEST_NAME_MAX = 300                               # characters
SUITE_NAME_MAX = 300
ROOT_CAUSE_MAX = 4_000
LOG_EXCERPT_MAX = 8_000
NOTE_MAX = 0                                      # no free "notes" field (nothing would read it)
CONFIDENCE_RANGE = (0, 100)
FAILURE_CATEGORIES = ("PRODUCT_BUG", "INFRASTRUCTURE", "TEST_DATA", "AUTOMATION_DEFECT", "FLAKY", "UNKNOWN")   # == models.postgres.FailureCategory (parity)
INPUT_KINDS = ("stored_run", "inline")            # AgentInvocationResponse.input_kind
UNTRUSTED_OPEN = "<<data>>"                       # the data-block markers; never an instruction, never a registered prompt
UNTRUSTED_CLOSE = "<</data>>"
WRAP_LIMITS = {                                   # the WRAPPED length of each seeded value == the cap the unchanged downstream code applies
    "test_name": 120,      # services/evidence_catalogue.py `_text(analysis.get("test_name"), 120)`
    "suite_name": 120,
    "root_cause": 200,     # agents/summary_agent.py `_usable_root_cause(analysis)[:200]`
    "excerpt": 150,        # services/evidence_catalogue.py `_EXCERPT_LIMIT = 150`
}                          # pinned by a test that reads those literals from source, so a later change there fails loudly
EXCERPTS_PER_FAILURE = 2                          # == evidence_catalogue.PER_TEST_LIMIT; the log excerpt is split into ≤ 2 wrapped slices
INLINE_ERRORS = {   # detail {"code","message"}
    "inline_input_too_large": "inline_input is larger than 262144 bytes.",                                   # 413
    "inline_input_unsupported": "{agent_id} does not accept inline input in this release; invoke it on a stored test run.",   # 422
    "inline_input_invalid": "inline_input does not match InlineSummaryInputV1.",                             # 422 (+ Pydantic "errors")
    "anchor_run_not_found": "No test run with that id in this project.",                                    # 404 — every wrong-anchor case, no oracle
    "inline_input_needs_anchor": "inline_input needs input.payload = {\"test_run_id\": \"<uuid>\"}: a stored test run of the project anchors it.",  # 422
}
INLINE_SENTENCES = {   # SPA copy
    "awaiting_review": "Awaiting review — a person must accept this output before it counts anywhere.",
    "excluded": "Inline-input output: kept out of the run's report, release gates, decisions, notifications and digests.",
    "stored_input_redacted": "Stored input (secrets and personal data redacted before storage)",
}
