"""The project export / import vocabulary (W24 ADM-05 phase 1: settings + catalog), in ONE place.

Every enum-like field the wave adds (a job's ``kind`` / ``status``, an entity's
class, an exclusion reason, a skip reason) is a plain ``str`` on the wire; the
values it may take live here as module constants. Every W24 4xx / 5xx answers
``detail = {"code": <key>, "message": ERROR_SENTENCES[<key>]}`` and a failed
job's ``error`` carries the same sentence. The frontend mirrors these values in
``frontend/src/types/projectData.ts`` (pinned by
``tests/test_project_export_vocab_parity.py``); ``JOB_KINDS`` / ``JOB_STATUSES``
are also the CHECK literal lists of migration ``0247`` (pinned by
``tests/test_migration_0247.py``).

Constants only: no ``app.*`` import, no session, no I/O
(``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

ARCHIVE_FORMAT = "testlookup.project-export"   # manifest["format"]; anything else → refused "not_a_project_export"
SCHEMA_VERSION = 1                             # manifest["schema_version"]; > this → "schema_newer"; < 1 / not int → "schema_unknown"
SUPPORTED_SCHEMA_VERSIONS = (1,)               # the import accepts exactly these
JOB_KINDS = ("export", "import")               # CHECK in 0247 uses the same literals
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "expired")   # CHECK in 0247; "expired" = export object removed by the janitor
TERMINAL_STATUSES = ("succeeded", "failed", "expired")
ENTITY_CLASSES = ("exported", "excluded", "not_project_scoped")
EXCLUSION_REASONS = (                          # every EXCLUDED table carries exactly one (contract §2.1)
    "run_history",          # test_runs and everything cascaded from / pointing at runs — phase 2 (F-1, ACT-03's format)
    "per_user_state",       # a person's own preferences / views / subscriptions / chats
    "audit_or_activity",    # audit, activity, usage and notification logs — they describe THIS deployment's past
    "job_or_outbox",        # job, lease, outbox and dispatch rows
    "access_grant",         # members, API keys, SCIM mappings — an archive never grants access
    "derived",              # recomputed from runs / catalog by a sweep (scores, snapshots, insights, windows)
    "ai_history",           # AI outputs, evaluations, generation batches, memory — evidence of the source's models
    "artefact_or_index",    # rows whose meaning is a MinIO object or a vector index (phase 2 with artefacts)
    "legal_hold",           # a retention hold is a legal act on the SOURCE's data
)
PART_ROWS_MAX = 5_000                          # rows per JSONL part
PART_BYTES_MAX = 8 * 1024 * 1024               # bytes per JSONL part (a row larger than this sits alone in its part)
ROW_BYTES_MAX = 4 * 1024 * 1024                # one serialised row; larger → export fails "row_too_large" (names table + count)
CURSOR_BATCH_ROWS = 1_000                      # server-side cursor fetch size (yield_per)
EXPORT_MAX_BYTES = 50 * 1024 * 1024            # == IMPORT_UPLOAD_MAX_BYTES: every archive this deployment writes can be imported
IMPORT_UPLOAD_MAX_BYTES = 50 * 1024 * 1024     # under nginx's 55m; the route stops reading past this → 413 "upload_too_large"
IMPORT_MAX_ENTRIES = 10_000                    # ZIP members (safe scan)
IMPORT_MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024   # 1 GiB across the archive
IMPORT_MAX_ENTRY_UNCOMPRESSED = 64 * 1024 * 1024     # one member
IMPORT_MAX_RATIO = 200                         # JSONL compresses far better than Allure's 100× cap
INSERT_BATCH_ROWS = 1_000                      # executemany batch on import
EXPORT_TTL_HOURS = 72                          # == retention_vocab.EXPORT_COLLECT_TTL_HOURS_DEFAULT (asserted); then the janitor removes the object
IMPORT_UPLOAD_TTL_HOURS = 1                    # the uploaded object is deleted by the worker when the job ends; the janitor removes strays after 1 h
JOB_STALE_MINUTES = 40                         # running longer than the global hard limit (task_time_limit=1860 s) + margin → janitor fails it "interrupted"
QUEUED_REDISPATCH_MINUTES = 10                 # a job still 'queued' after this (the enqueue after commit was lost) is re-dispatched, ≤ 3 attempts, then "interrupted"
JOBS_LIST_LIMIT = 10                           # the page's recent-jobs read
STORAGE_PREFIX_EXPORT = "project-exports/"     # COMPLIANCE_BUCKET keys: project-exports/YYYY/MM/DD/<project_id>/<job_id>.zip
STORAGE_PREFIX_IMPORT = "project-imports/"     # project-imports/YYYY/MM/DD/<job_id>.zip (the upload)
IMPORTED_NAME_SUFFIX = " (imported)"           # "<name> (imported)", then " (imported 2)", " (imported 3)" … (≤ 50 tries, then "name_taken")
IMPORTED_SLUG_SUFFIX = "-imported"             # "<slug>-imported", "-imported-2" …; trimmed to fit Project.slug's length
REDACTED = "[REDACTED]"                        # == redaction_service.REDACTED (asserted)
SECRET_REQUIRED = "Secret required — add it, then switch this on."   # disabled_reason / note on every imported sender
IMPORTED_PAUSE_REASON = "Imported — resume when this project receives runs."   # run_cadences.paused_reason (≤ run_cadence_vocab.PAUSE_REASON_MAX, asserted)
IMPORTED_PAUSE_DAYS = 90                       # == run_cadence_vocab.PAUSE_MAX_DAYS (asserted)
ERROR_SENTENCES = {   # detail = {"code": <key>, "message": <sentence>} on every W24 4xx/5xx; the job's `error` uses the same text
    "job_not_found": "Project data job not found.",                                     # 404 — another project's / requester's alike
    "export_running": "An export of this project is already running.",                  # 409 (one queued/running export per project)
    "export_not_ready": "This export has not finished yet.",                            # 409 on download while queued/running
    "export_failed": "This export failed; start a new one.",                            # 409 on download of a failed job
    "export_expired": "This export was removed after 72 hours; start a new one.",       # 410 on download
    "not_an_export": "Only an export has a download.",                                  # 409 on an import job's download
    "archive_unreadable": "The archive could not be read from storage. Try again.",     # 502 (== retention's sentence)
    "upload_too_large": "The file is larger than 50 MB.",                               # 413
    "upload_empty": "Choose a .zip file exported from TestLookup.",                     # 422
    "not_a_zip": "The file is not a valid ZIP archive.",                                # 422 at the route (magic bytes) and in the job
    "not_a_project_export": "This ZIP is not a TestLookup project export (manifest.json is missing or of another kind).",
    "schema_newer": "This export was written by a newer TestLookup (schema {found}); this deployment reads schema {supported}. Upgrade, then import again.",
    "schema_unknown": "This export's schema version is not one this deployment can read.",
    "tampered": "The archive does not match its manifest ({reason}); nothing was imported.",
    "signature_invalid": "The archive's signature is not valid; nothing was imported.",
    "unsafe_archive": "The archive was refused before reading: {message}",              # safe-scan codes: unsafe_path, nested_zip, zip_bomb, zip_too_large, too_many_entries
    "invalid_row": "{entity} row {line} in {file} is not valid ({field}); nothing was imported.",
    "unknown_entity": "The archive contains {entity}, which this deployment does not import; nothing was imported.",
    "unknown_column": "This export has {entity}.{column}, which this deployment does not know — it was written by a newer TestLookup. Upgrade, then import again.",
    "name_invalid": "A project name of 2 to 255 characters is required.",               # == ProjectCreate.name bounds (asserted)
    "name_taken": "A project called {name} already exists; choose another name.",
    "client_token_invalid": "client_token must be a UUID.",
    "import_failed": "The import failed and was rolled back; no project was created ({error_type}).",
    "interrupted": "The job was interrupted; nothing was kept. Start it again.",
    "export_build": "The export could not be built ({error_type}).",
    "export_write": "The export could not be written to storage ({error_type}).",
    "export_unverified": "The stored export could not be verified ({reason}).",
    "export_too_large": "This project's export is larger than 50 MB, the import limit.",
    "row_too_large": "{entity} has {count} rows larger than 4 MB; they cannot be exported.",
}
UI_SENTENCES = {      # the frontend renders these verbatim (React text, never HTML)
    "export_intro": "Download this project's settings and test catalog as a ZIP. Run history, logs and attachments are not included.",
    "import_intro": "Create a NEW project from an export. Nothing is merged into an existing project.",
    "import_admin_only": "Importing creates a project, so it is limited to administrators.",
    "inert_note": "Webhooks, team channels, Git integrations and notification rules arrive switched off; run cadences arrive paused for 90 days.",
    "no_export": "No export of this project yet.",
    "export_running": "Exporting… {done} of {total} tables",
    "export_ready": "Download ({size})",
    "export_expires": "Available until {date}.",
    "import_running": "Importing… {done} of {total} tables",
    "import_done": "Created {name}.",
    "skipped_one": "1 row was skipped: {reason}",
    "skipped_many": "{count} rows were skipped: {reason}",
    "cleared_one": "1 assignment was cleared",
    "cleared_many": "{count} assignments were cleared",
    "load_exports_failed": "Could not load this project's exports.",
    "load_imports_failed": "Could not load your imports.",
    "export_heading": "Export settings and catalog",
    "import_heading": "Import as a new project",
    "below_qa_lead_title": "QA lead access required",
    "below_qa_lead_body": "Project data is limited to QA leads and administrators.",
}
SKIP_REASONS = {      # import_report.skipped[*] / cleared[*].reason → the sentence after "… were skipped: " / "… were cleared: "
    "user_not_found": "the person does not exist here",            # a user_refs column set NULL (cleared[])
    "assignment": "the new project has no other members yet",       # a `cleared` column that was non-null (cleared[])
    "row_unresolved": "it pointed at a row that is not in the export",   # optional FK → NULL (cleared[]); required FK → row skipped (skipped[])
}
REVIEW_RESET_STATES = ("review_requested", "under_review")   # managed_test_cases.status arriving as "draft" (contract §2.1 row 3)
