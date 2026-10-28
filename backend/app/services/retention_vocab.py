"""The retention-hold and keep-last-floor vocabularies (W22a ACT-05 / ACT-04), in ONE place.

Every enum-like field the wave adds (``entity_type``, ``state``) is a plain
``str`` on the wire; the values it may take live here as module constants, and
a request value outside them is refused with one fixed sentence
(``HOLD_ERROR_SENTENCES``). The frontend mirrors these values in
``frontend/src/types/retentionHolds.ts`` (pinned by
``tests/test_retention_vocab_frontend_parity.py``); ``HOLD_ENTITY_TYPES`` is
also the CHECK literal list of migration ``0239`` (pinned by
``tests/test_migrations_0239_0240.py``).

Constants only: no ``app.*`` import, no session, no I/O
(``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

HOLD_ENTITY_TYPES = ("project", "release", "run", "compliance_pack")   # CHECK in 0239 uses the same literals
HOLD_STATES = ("live", "expired", "released")      # derived on read from released_at / expires_at / now; never stored
HOLD_REASON_MIN, HOLD_REASON_MAX = 3, 500           # place and release reasons alike (trimmed length)
HOLD_MAX_EXPIRY_DAYS = 3650                         # expires_at ∈ (now, now + 3650 d]
HOLD_LIST_DEFAULT_LIMIT, HOLD_LIST_MAX_LIMIT = 50, 200
HELD_SENTENCE = "Held: {reason}"                    # the ONE refusal sentence (409s, criteria blocked[], job error)
PROJECT_HOLD_LABEL = "the whole project"            # entity_label of a project hold
HOLD_ERROR_SENTENCES = {                            # detail = {"code": <key>, "message": <sentence>} on the hold routes' own 4xx
    "unknown_entity_type": "Unknown entity type. Valid: project, release, run, compliance_pack.",
    "entity_id_required": "entity_id is required for a release, run or compliance_pack hold.",
    "entity_id_forbidden": "A project hold covers the whole project; leave entity_id empty.",
    "entity_not_found": "That release, run or compliance pack is not in this project.",   # 404; missing and foreign alike (no oracle)
    "reason_length": "A reason of 3 to 500 characters is required.",
    "expiry_not_future": "expires_at must be in the future.",
    "expiry_too_far": "expires_at may be at most 3650 days ahead.",
    "hold_not_found": "Hold not found.",                                                   # 404; another project's hold alike
    "hold_not_live": "This hold is no longer live; it was released or it expired.",        # 409
    "unknown_state": "Unknown state. Valid: live, expired, released, all.",
    "interactive_login_required": "Holds are placed and released by a signed-in person; API keys can only read them.",  # 403
}
KEEP_LAST_RUNS_DEFAULT, KEEP_LAST_RUNS_MIN, KEEP_LAST_RUNS_MAX = 10, 0, 1000
FLOOR_NOTE_EXPLICIT = "Explicit deletions are not limited by the keep-last floor; it applies to the scheduled purge only."
REFERENCE_LABELS = {                                 # copy only — the FK LIST itself comes from Base.metadata (§2.2 F-3)
    "run_baselines.baseline_run_id": "{count} runs lose their baseline link",
    "run_diffs.baseline_run_id": "{count} run diffs lose their baseline run",
    "compliance_packs.test_run_id": "{count} compliance packs lose their run link",
}
REFERENCE_LABELS_ONE = {                             # the same copy for a count of 1 ("1 compliance pack loses ...")
    "run_baselines.baseline_run_id": "1 run loses its baseline link",
    "run_diffs.baseline_run_id": "1 run diff loses its baseline run",
    "compliance_packs.test_run_id": "1 compliance pack loses its run link",
}
REFERENCE_FALLBACK_LABEL = "{count} {table} rows lose their {column} link"   # every other derived FK
REFERENCE_FALLBACK_LABEL_ONE = "1 {table} row loses its {column} link"
BASELINE_PURGED_SENTENCE = "Baseline build {build} was purged on {date}."     # the run page (date = the viewer's locale date)
