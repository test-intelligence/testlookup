"""The project export / import vocabulary (W24 ADM-05 phase 1: settings + catalog; W30 phase 2: run history), in ONE place.

Every enum-like field the wave adds (a job's ``kind`` / ``status``, an entity's
class, an exclusion reason, a skip reason) is a plain ``str`` on the wire; the
values it may take live here as module constants. Every W24 4xx / 5xx answers
``detail = {"code": <key>, "message": ERROR_SENTENCES[<key>]}`` and a failed
job's ``error`` carries the same sentence. The frontend mirrors these values in
``frontend/src/types/projectData.ts`` (pinned by
``tests/test_project_export_vocab_parity.py``); ``JOB_KINDS`` / ``JOB_STATUSES``
are also the CHECK literal lists of migrations ``0247`` / ``0252`` (pinned by
``tests/test_migration_0247.py`` / ``tests/test_migration_0252.py``).

One format, two schema versions (W30): an export WITHOUT history writes
``CATALOG_SCHEMA_VERSION`` (phase 1's entities and columns, so a phase-1
deployment still imports it); an export WITH history writes ``SCHEMA_VERSION``.

Constants only: no ``app.*`` import, no session, no I/O
(``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

ARCHIVE_FORMAT = "testlookup.project-export"   # manifest["format"]; anything else → refused "not_a_project_export"
SCHEMA_VERSION = 2                             # written by an export WITH history; > max(SUPPORTED) → "schema_newer"; < 1 / not int → "schema_unknown"
CATALOG_SCHEMA_VERSION = 1                     # written by an export WITHOUT history (phase 1's entities + columns; a phase-1 importer reads it)
SUPPORTED_SCHEMA_VERSIONS = (1, 2)             # the import accepts exactly these
JOB_KINDS = ("export", "import")               # CHECK in 0247 uses the same literals
JOB_STATUSES = ("uploading", "queued", "running", "succeeded", "failed", "expired")   # CHECK in 0252 (0247 + "uploading" = a parted import upload); "expired" = export object removed by the janitor
TERMINAL_STATUSES = ("succeeded", "failed", "expired")
JOB_PHASES = ("catalog", "runs", "documents", "objects", "verify", "index")   # progress.phase (export: catalog→runs→documents→objects→verify; import: + index)
ENTITY_CLASSES = ("exported", "excluded", "not_project_scoped")
EXCLUSION_REASONS = (                          # every EXCLUDED table carries exactly one (contract §2.1)
    "run_history",          # RETIRED by W30 (no table carries it; a phase-1 manifest's `excluded` block still names it)
    "per_user_state",       # a person's own preferences / views / subscriptions / chats
    "audit_or_activity",    # audit, activity, usage and notification logs — they describe THIS deployment's past
    "job_or_outbox",        # job, lease, outbox and dispatch rows
    "access_grant",         # members, API keys, SCIM mappings — an archive never grants access
    "derived",              # recomputed from runs / catalog by a sweep (scores, snapshots, insights, windows)
    "ai_history",           # AI outputs, evaluations, generation batches, memory — evidence of the source's models
    "artefact_or_index",    # rows whose meaning is a MinIO object or a vector index (phase 2 with artefacts)
    "legal_hold",           # a retention hold is a legal act on the SOURCE's data
    "ingest_machinery",     # outboxes, live-ingest receipts / checkpoints / sessions, agent invocations — queues a sweep would pick up
    "external_tracker",     # defects and defect candidates — rows whose meaning is a ticket in the SOURCE's Jira; a sync would call out
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
# ── W30 ADM-05 phase 2: run history (contract §1.1) ─────────────────────────────────────────────────────────────
HISTORY_WINDOWS_DAYS = (30, 90, 365)           # the page's choices; None = all history
HISTORY_DEFAULT_DAYS = 90
HISTORY_MAX_RUNS = 2_000                       # newest first inside the window; older runs are not exported (counted "runs_over_cap")
HISTORY_RUN_STATUSES = ("PASSED", "FAILED", "STOPPED")      # == LaunchStatus minus IN_PROGRESS (asserted)
TERMINAL_PIPELINE_STATUSES = ("completed", "passed", "failed")   # ⊂ PipelineRunStatus (asserted)
HISTORY_EXPORT_MAX_BYTES = 2 * 1024**3         # == IMPORT_ARCHIVE_MAX_BYTES: every history archive this deployment writes can be imported
IMPORT_ARCHIVE_MAX_BYTES = 2 * 1024**3         # sum of the parts of ONE parted upload
IMPORT_PART_MAX_BYTES = 32 * 1024**2           # one PUT body; under nginx 55m / ingress 100m (contract §0.5)
IMPORT_MAX_PARTS = 64                          # 64 × 32 MiB = 2 GiB
IMPORT_HISTORY_MAX_ENTRIES = 40_000            # ZIP members of a schema-2 archive == HISTORY_MAX_MEMBERS
HISTORY_MAX_MEMBERS = 40_000                   # the manifest lists every member (≈ 190 bytes each) under both verifiers' MAX_MANIFEST_BYTES (asserted)
IMPORT_HISTORY_MAX_TOTAL_UNCOMPRESSED = 8 * 1024**3
IMPORT_HISTORY_MAX_ENTRY_UNCOMPRESSED = 256 * 1024**2      # == OBJECT_MAX_BYTES
IMPORT_HISTORY_MAX_RATIO = 200                 # per member, as phase 1 (objects are STORED, ratio ≈ 1)
OBJECT_MAX_BYTES = 256 * 1024**2               # a larger object is NOT copied: manifest history.skipped (reason "too_large")
OBJECT_CHUNK_BYTES = 1024**2                   # streamed object read / write chunk
OBJECT_KEY_SUFFIX_MAX = 512                    # a source key suffix under its run root; longer → object skipped "unsafe_key"
OBJECT_KEY_SUFFIX_RE = r"^(?!/)(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9._~@+=,()\[\] /-]{1,512}$"   # no leading '/', no '.' / '..' segment
MONGO_BATCH_DOCS = 500                         # cursor batch_size on export, insert_many batch on import
MONGO_DOC_MAX_BYTES = 4 * 1024**2              # one serialised document (== ROW_BYTES_MAX); larger → export fails "row_too_large"
MONGO_MAX_DEPTH = 32                           # nesting deeper → invalid_document (BSON allows 100; nothing the product writes nests past 12)
HISTORY_TIME_LIMIT_SECONDS = 3 * 3600          # apply_async(time_limit=…) of a history export / import (soft = this − 300)
HISTORY_JOB_STALE_MINUTES = 200                # janitor: a running HISTORY job older than this → "interrupted"
UPLOAD_STALE_HOURS = 24                        # janitor: an "uploading" job older than this → failed "interrupted", its parts removed
DIGEST_LOOKBACK_DAYS = 7                       # newest imported run within this of the import → recent_runs_note (contract §0.9)
STORAGE_PREFIX_IMPORT_PARTS = "project-imports/parts/"      # project-imports/parts/<job_id>/part-0001 …
IMPORTED_PURGE_PAUSE_DAYS = 90                 # == retention_vocab.PAUSE_DAYS_MAX (asserted): a history import pauses the copy's purge (§2.7)
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
    # ── W30 run history + the parted upload ──
    "history_too_large": "This export with run history is larger than 2 GB. Choose a shorter window.",
    "history_window_invalid": "Choose 30, 90 or 365 days, or all history.",
    "max_runs_invalid": "max_runs must be between 1 and 2000.",
    "upload_parts_invalid": "An upload has 1 to 64 parts of at most 32 MB and at most 2 GB in all.",
    "part_too_large": "A part is larger than 32 MB.",
    "part_number_invalid": "Part {part} is not part of this upload (1 to {parts}).",
    "part_digest_mismatch": "Part {part} does not match its SHA-256; send it again.",
    "part_conflict": "Part {part} was already received with different content.",
    "upload_incomplete": "The upload is missing parts {missing}.",
    "upload_digest_mismatch": "The assembled file does not match the SHA-256 given when the upload started.",
    "upload_not_open": "This upload is no longer accepting parts.",
    "invalid_document": "{collection} document {line} in {file} is not valid ({reason}); nothing was imported.",
    "object_unreadable": "An object in the archive could not be written to storage ({error_type}); nothing was imported.",
    "documents_unwritable": "The documents could not be written ({error_type}); nothing was imported.",
    "upload_too_large_history": "The file is larger than 2 GB.",   # the parted route / the card's file check (the 50 MB sentence stays for the single route)
}
UI_SENTENCES = {      # the frontend renders these verbatim (React text, never HTML)
    "export_intro": "Download this project's settings and test catalog as a ZIP, with run history if you choose.",
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
    "export_heading": "Export project data",
    "import_heading": "Import as a new project",
    "below_qa_lead_title": "QA lead access required",
    "below_qa_lead_body": "Project data is limited to QA leads and administrators.",
    # ── W30 run history ──
    "include_history": "Include run history",
    "history_window": "Runs from the last",
    "history_intro": "Runs, test results, AI analyses, stored documents and attachments. Raw report uploads and pipeline scratch files are not included.",
    "estimate": "About {size} · {runs} runs · {documents} documents · {objects} attachments",
    "estimate_unmeasured": "Size unknown: {store} could not be read.",
    "estimate_over": "Larger than 2 GB — choose a shorter window.",
    "phase_catalog": "Settings and catalog",
    "phase_runs": "Runs and results",
    "phase_documents": "Stored documents",
    "phase_objects": "Attachments",
    "phase_verify": "Verifying",
    "phase_index": "Search index",
    "progress_phase": "{phase}… {done} of {total}",
    "uploading": "Uploading… {done} of {total} parts",
    "history_imported": "{runs} runs from {oldest} to {newest}",
    "retention_note": "Runs older than this project's retention window ({days} days) will be purged on the next nightly purge.",
    "retention_off_note": "Retention is off for this project: imported runs are kept.",
    "recent_runs_note": "{count} imported runs are from the last 7 days; an all-projects digest can list them.",
    "reindex_queued": "Search is being rebuilt for the new project.",
    "reindex_not_queued": "Search could not be rebuilt automatically — use Reindex on the Search page.",
    "resume_upload": "Choose the same file to resume the upload.",
    "large_download_hint": "Large archives download more reliably with the CLI: testlookup projects export {project} --history",
}
SKIP_REASONS = {      # import_report.skipped[*] / cleared[*].reason → the sentence after "… were skipped: " / "… were cleared: "
    "user_not_found": "the person does not exist here",            # a user_refs column set NULL (cleared[])
    "assignment": "the new project has no other members yet",       # a `cleared` column that was non-null (cleared[])
    "row_unresolved": "it pointed at a row that is not in the export",   # optional FK → NULL (cleared[]); required FK → row skipped (skipped[])
    "retired_table": "the table no longer exists in this version",    # a RETIRED_ENTITIES table listed with rows (skipped[], column null)
    # ── W30 run history (entity = a table, a collection or "objects") ──
    "run_not_exported": "its run is not in the export",                       # a history row / document / object whose run was not exported
    "pipeline_not_terminal": "its AI pipeline was still running at export",   # pipeline + stage results skipped at export (manifest)
    "unsafe_key": "its storage key is not a safe path",                       # object skipped (export or import)
    "too_large": "it is larger than 256 MB",                                   # object skipped at export
    "invalid_document": "it is not a valid stored document",                   # Mongo document refused at import → the WHOLE import fails
    "pipeline_scratch": "it pointed at a pipeline scratch file, which is not exported",   # evidence_artifacts.uri_or_ref written NULL at export (manifest)
}
#: Tables a schema_version 1 archive may list that this deployment dropped (W25d HYG-02). Every W24 export
#: lists all its tables, empty ones included: with 0 rows such a table is ignored silently; with N rows its
#: parts are never read and the report says N rows were skipped ("retired_table"). Any other unknown table is
#: still refused "unknown_entity".
RETIRED_ENTITIES = ("quality_gates",)
REVIEW_RESET_STATES = ("review_requested", "under_review")   # managed_test_cases.status arriving as "draft" (contract §2.1 row 3)
