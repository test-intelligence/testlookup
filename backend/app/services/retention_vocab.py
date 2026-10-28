"""The retention-hold and keep-last-floor vocabularies (W22a ACT-05 / ACT-04), the
export-before-delete and purge-schedule / instance-default vocabularies (W22b
ACT-03 / ACT-06), in ONE place.

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

# ── W22b ACT-03: export before delete ────────────────────────────────────────
# Mirrored by frontend/src/types/retentionExport.ts (pinned by
# tests/test_retention_export_vocab_frontend_parity.py); EXPORT_TIERS /
# EXPORT_TARGETS are also the CHECK literal lists of migration 0241.
EXPORT_TIERS = ("summary", "evidence", "full")        # == compliance_pack_service._EXPORT_TIERS (asserted by the parity test)
EXPORT_TIER_DEFAULT = "summary"
EXPORT_TARGETS = ("collect", "retain")                # the epic's "external" stays cut
EXPORT_MAX_RUNS = 2000                                # == compliance_pack_service.MAX_EXPORT_RUNS (asserted)
EXPORT_MAX_BYTES_DEFAULT = 536_870_912                # 512 MiB; settings.RETENTION_EXPORT_MAX_BYTES overrides
EXPORT_COLLECT_TTL_HOURS_DEFAULT = 72                 # settings.RETENTION_EXPORT_COLLECT_TTL_HOURS overrides (1-720)
EXPORT_SPOOL_BYTES = 16 * 1024 * 1024                 # SpooledTemporaryFile threshold (memory above it goes to disk)
EXPORT_TEXT_OBJECT_MAX_BYTES = 25 * 1024 * 1024       # `full`: a larger object is listed, not copied
EXPORT_TEXT_SUFFIXES = (".json", ".xml", ".txt", ".log", ".html", ".csv", ".yaml", ".yml", ".md")
EXPORT_VERIFY_ATTEMPTS = 3                            # re-read attempts on a storage timeout / 5xx (30 s, 60 s, 120 s backoff)
EXPORT_AUDIT_KEY_PREFIX = "deletion_export:"          # settings_audit_log.setting_key = f"{prefix}{project_id}" (never purged)
ARCHIVE_NOT_BACKUP = "Archive, not a backup — it cannot be re-imported."
EXPORT_FULL_WARNING = ("Full copies raw payloads and text attachments after secret redaction; binary attachments "
                       "such as screenshots and ZIP files are listed, not copied. It can be large.")
EXPORT_RETAIN_NOTE = "A retained archive stays in this deployment's object store, so only the difference is reclaimed."
EXPORT_COLLECT_NOTE = "Download the archive, then confirm you have saved it; nothing is deleted before you confirm."
EXPORT_ERROR_SENTENCES = {    # detail = {"code": <key>, "message": <sentence>} on every export 4xx (apiErrors reads it)
    "unknown_tier": "Unknown export tier. Valid: summary, evidence, full.",
    "unknown_target": "Unknown export target. Valid: collect, retain.",
    "too_many_runs": "An archive covers at most 2000 runs; narrow the criteria.",
    "nothing_to_export": "There are no runs to archive in this set.",
    "over_cap": "This archive would be over the size limit; choose a smaller tier or narrow the criteria.",
    "job_not_found": "Deletion job not found.",                         # 404; another project's job alike (no oracle)
    "not_awaiting": "This job is not waiting for its archive to be collected.",
    "collect_expired": "The collection window has closed; nothing was deleted. Preview again to start over.",
    "not_collected": "Download the archive before confirming you have saved it.",
    "manifest_mismatch": "That manifest checksum is not this job's archive; download it again.",
    "archive_unreadable": "The archive could not be read from storage. Try again.",   # 502
    "interactive_login_required": "An archive is collected and confirmed by a signed-in administrator.",   # 403
}
EXPORT_JOB_ERRORS = {          # DeletionJob.error text (every one says nothing was deleted)
    "build": "Export failed — nothing was deleted: the archive could not be built ({error_type}).",
    "over_cap": "Export failed — nothing was deleted: the archive passed the {limit_mb} MB limit.",
    "write": "Export failed — nothing was deleted: the archive could not be written to storage ({error_type}).",
    "unverified": "Export failed — nothing was deleted: the stored archive could not be verified ({reason}).",
    "holds_unreadable": "Export failed — nothing was deleted: retention holds could not be read.",
    "expired": "Not collected within {hours} hours — nothing was deleted; the archive was removed.",
    "interrupted": "Export interrupted — nothing was deleted.",
}
# The "expired" sentence for a one-hour window (RETENTION_EXPORT_COLLECT_TTL_HOURS=1 read "within 1 hours").
EXPORT_JOB_EXPIRED_ONE = "Not collected within 1 hour — nothing was deleted; the archive was removed."

# ── W22b ACT-06: purge window, pause, instance default ───────────────────────
INHERITABLE_FIELDS = ("enabled", "raw_events_days", "runs_days", "artifacts_days", "audit_days",
                      "keep_last_runs", "purge_window_start_utc")
FIELD_SOURCES = ("project", "instance", "default")    # RetentionPolicyRead.field_sources values
PURGE_WINDOW_DEFAULT_HOUR, PURGE_WINDOW_MIN, PURGE_WINDOW_MAX = 2, 0, 23
PAUSE_DAYS_DEFAULT, PAUSE_DAYS_MIN, PAUSE_DAYS_MAX = 7, 1, 90
PURGE_LOCK_KEY = "testlookup:lock:retention-purge:{project_id}"
PURGE_LOCK_TTL_SECONDS = 2400                         # > the 1860 s hard task limit (celery_app.py) + margin
INSTANCE_POLICY_AUDIT_KEY = "retention_instance_policy"
INSTANCE_ENABLE_PHRASE = "enable retention for {count} projects"
INSTANCE_ENABLE_PHRASE_ONE = "enable retention for 1 project"
INSTANCE_PREVIEW_MAX_ITEMS = 500                      # the preview lists <= 500 projects and ALWAYS returns `total`
POLICY_ERROR_SENTENCES = {     # 422 / 409 detail {"code","message"} on the policy and instance routes
    "unknown_inherit_field": (
        "Unknown field in inherit. Valid: enabled, raw_events_days, runs_days, artifacts_days, audit_days, "
        "keep_last_runs, purge_window_start_utc."
    ),
    "inherit_conflict": "A field cannot be both set and inherited in one save.",
    "pause_conflict": "Send pause_days or resume, not both.",
    "enable_via_flow": "Turning the instance default on needs its preview and a typed confirmation.",
    "preview_stale": "The projects this would switch on changed since the preview; preview again.",
    "confirmation_mismatch": "Type the confirmation exactly as shown.",
    "instance_interactive_only": (
        "The instance default is changed by a signed-in administrator; API keys can only read it."
    ),
    "already_enabled": "The instance default is already on.",                       # 409 on a second enable
}
INHERITED_LABEL = "{value} (instance default)"        # the marker text when the source is "instance"
DEFAULT_LABEL = "{value} (default)"                   # ... when the source is "default"
PAUSED_SENTENCE = "Paused until {date}"               # date = the viewer's locale date-time
NEXT_PURGE_SENTENCE = "Next scheduled purge {date}"
