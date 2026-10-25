"""Agent-ops vocabularies and sentences (W14: AGT-05, AGT-08, AGT-12, AGT-07).

One place for every word and sentence the W14 routes, services, schemas and
tests share, so a refusal the API sends and the sentence a test asserts cannot
drift apart. Pure: imports nothing from ``app``.

Enum-like request / response fields are plain ``str`` everywhere; each
vocabulary below is the ONE definition of its values, judged in the router or
service with one fixed 422 sentence. The frontend mirrors the few it renders in
``frontend/src/types/agentOps.ts`` (``COMPARE_OUTPUT_STATES``,
``BULK_JOB_STATES``, ``WORKFLOW_DEFAULT_BASES`` and
``agent_config_service.TIER_ORDER``), pinned by
``tests/test_agent_ops_vocab_frontend_parity.py``.

Nothing here logs.
"""
from __future__ import annotations

# ── AGT-05 compare ───────────────────────────────────────────────────────────
COMPARE_FIELDS: tuple[str, ...] = (
    "status", "duration_seconds", "total_tokens", "cost_usd", "fallback_used",
    "confidence_score", "execution_path", "output_checksum_sha256",
)
#: identical: every shared stage's checksums equal, no presence diff.
COMPARE_OUTPUT_STATES: tuple[str, ...] = ("identical", "changed", "unknown")
#: Per side (the agentic-runtime cap).
MAX_COMPARE_STAGES = 200
COMPARE_SAME_ID = "Choose two different pipeline runs to compare."
COMPARE_CROSS_PROJECT = "These pipeline runs belong to different projects: compare pipelines of one project."

# ── AGT-08 project default workflows ────────────────────────────────────────
WORKFLOW_DEFAULT_BASES: tuple[str, ...] = ("offline", "deep")
WORKFLOW_SOURCES: tuple[str, ...] = ("named", "project_default", "builtin", "default_unavailable")
DEFAULT_BASE_INVALID = "base must be offline or deep."
DEFAULT_NOT_PUBLISHED = "Only a published workflow can be the default: publish {workflow_id} first."
DEFAULT_BASE_MISMATCH = "{workflow_id} is built on {actual}: it can only be the {actual} default."
DEFAULT_UNAVAILABLE_GONE = "The default {ref} no longer resolves in this project."
DEFAULT_UNAVAILABLE_BASE = "The default {ref} is no longer built on {base}."
DEFAULT_UNAVAILABLE_INVALID = "The default {ref} no longer passes validation: {first_error}"
#: Appended to one of the three DEFAULT_UNAVAILABLE_* sentences in the pipeline's recorded reason.
DEFAULT_FALLBACK_SUFFIX = " The built-in {base} workflow ran instead."

# ── AGT-12 bulk jobs ─────────────────────────────────────────────────────────
BULK_JOB_KEY = "testlookup:bulk-job:{job_id}"                      # HASH, TTL BULK_JOB_TTL_SECONDS
BULK_CLIENT_KEY = "testlookup:bulk-job:client:{user_id}:{token}"   # STRING job id, TTL BULK_CLIENT_TOKEN_TTL_SECONDS (SET NX)
BULK_JOB_TTL_SECONDS = 86_400
BULK_CLIENT_TOKEN_TTL_SECONDS = 600
#: The run_ids cap of BulkTriggerRequest, one number.
BULK_FILTER_CAP = 2_000
#: run_agent_pipeline's time_limit: after it a not-started task never starts.
BULK_JOB_SETTLE_SECONDS = 1_800
BULK_SOURCES: tuple[str, ...] = ("selection", "filter")
BULK_JOB_STATES: tuple[str, ...] = ("active", "finished", "cancelled")
BULK_COUNTER_SOURCES: tuple[str, ...] = ("redis", "postgres")
BULK_IDS_OR_FILTER = "Send run_ids or a run_filter, not both."
BULK_NOTHING_TO_TRIGGER = "Send run_ids or a run_filter."
BULK_JOB_NOT_FOUND = "Bulk job not found"
BULK_JOB_ALREADY_FINISHED = "This bulk job has already finished."
BULK_NOT_STARTED_NOTE = (
    "{n} queued pipelines never started (a duplicate request, the cancelled job or a worker "
    "restart dropped them). None of them is counted as failed."
)

# ── AGT-07 deep investigation ───────────────────────────────────────────────
PRESCAN_LIMIT_DEFAULT, PRESCAN_LIMIT_MAX, PRESCAN_MEMBERS_CAP = 10, 50, 500
PRESCAN_REFINEMENTS: tuple[str, ...] = ("clusters", "signature_only")
PRESCAN_NOTE_CLUSTERS = "Grouped by error signature; the last deep analysis's clusters are shown beside each group."
PRESCAN_NOTE_SIGNATURE_ONLY = "Grouped by error signature: no embedding clustering has run for this run yet."
HISTORY_SIZE_DEFAULT, HISTORY_SIZE_MAX = 10, 50
HISTORY_NEEDS_PROJECT = "Choose a project: deep investigation history is per project."
#: The project's latest completed / passed deep pipelines.
ESTIMATE_SAMPLE_PIPELINES = 20
#: Scaled by failures analysed; every other stage is a p50 as measured.
PER_FAILURE_STAGES: tuple[str, ...] = ("root_cause_analysis",)
ESTIMATE_UNMEASURED = (
    "No completed deep investigation in this project yet: there is no measured time or cost "
    "to estimate from."
)
ESTIMATE_UNPRICED = "No price is known for {provider} / {model}: the cost cannot be estimated."
ESTIMATE_LOCAL_NOTE = "local model — no charge"
ESTIMATE_NO_ENDPOINT = "No model endpoint is allowed for tier {tier} on this deployment."
ESTIMATE_SELECTED_TOO_MANY = "selected_failures is more than this run's {n} failing results."
#: The FE note, mirrored.
RUN_CONFIG_TOO_MANY_FAILURES = "A run configuration takes at most 500 failures: select fewer groups."
#: The agent whose tier / cap / tools a deep run configuration tightens.
RUN_CONFIG_AGENT_ID = "agent.root_cause_analysis.v1"
RUN_CONFIG_FAILURE_IDS_MAX = 500
RUN_CONFIG_DEEP_ONLY = "A run configuration applies to a deep investigation only."
RUN_CONFIG_FAILURES_OUTSIDE = "{n} of the selected failures are not failing results of this run."
RUN_CONFIG_TIER_INVALID = "model_tier must be deterministic, slm, llm or auto."
#: agent_invoke.py's text, verbatim.
RUN_CONFIG_TIGHTEN_ONLY = "config_overrides may only tighten the project agent configuration"
DEEP_ALREADY_IN_PROGRESS = "Deep investigation already in progress"
