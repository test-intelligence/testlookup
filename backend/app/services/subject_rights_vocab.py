"""The subject-rights vocabulary (W31 STRAT-12: per-person export, Art. 15 / 20; pseudonymising erasure, Art. 17), in ONE place.

Every enum-like field the wave adds (a job's ``kind`` / ``status`` / ``source`` /
``phase``, an inventory location's ``export_as`` / ``erase`` action) is a plain
``str`` on the wire; the values it may take live here as module constants.
Every W31 4xx / 5xx answers ``detail = {"code": <key>, "message":
ERROR_SENTENCES[<key>]}`` and a failed job's ``error`` carries the same
sentence. The frontend mirrors these values in
``frontend/src/types/subjectRights.ts`` (pinned by
``tests/test_subject_rights_vocab_parity.py``); ``JOB_KINDS`` /
``JOB_STATUSES`` / ``ERASURE_PHASES`` are also the CHECK literal lists of
migration ``0253`` (pinned by ``tests/test_migration_0253.py``);
``REQUEST_SOURCES`` (with ``scim``), ``ERASURE_REQUEST_STATUSES`` and
``DECLINE_CODES`` are the CHECK literals of migration ``0256`` (W38, pinned by
``tests/test_migration_0256.py``).

The four values copied from ``project_export_vocab`` (``EXPORT_TTL_HOURS``,
``PART_ROWS_MAX``, ``PART_BYTES_MAX``, ``JOB_STALE_MINUTES``) are asserted equal
by the parity test, never imported.

Constants only: no ``app.*`` import, no session, no I/O
(``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

EXPORT_FORMAT = "testlookup.subject-export"     # manifest["format"]
SCHEMA_VERSION = 1                              # manifest["schema_version"]
JOB_KINDS = ("export", "erasure")               # CHECK in 0253 uses the same literals
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "expired")   # "expired" = export object removed (janitor / erasure)
TERMINAL_STATUSES = ("succeeded", "failed", "expired")
LIVE_STATUSES = ("queued", "running")           # the two partial uniques of 0253 (one live job per subject and kind)
ERASURE_PHASES = ("lockout", "postgres", "objects", "redis", "done")     # job.phase = the LAST phase committed; CHECK in 0253
REQUEST_SOURCES = ("self", "admin", "scim")     # who asked (job.source); an erasure is "admin" or "scim" (W38 D-11, 0256)
SOURCE_SCIM = "scim"                            # an erasure started by an IdP's SCIM DELETE (requested_by_id NULL)
ERASE_ACTIONS = ("delete", "pseudonymise", "reference", "keep_audit", "keep_record", "out_of_scope", "no_personal_data")
EXPORT_AS = ("full", "metadata", "reference", "none")
PSEUDONYM_PREFIX = "erased-"                    # username = "erased-" + user_id.hex[:n]
PSEUDONYM_HEX_LENGTHS = (8, 12, 16, 32)         # tried in order while another row already holds the username
ERASED_EMAIL_DOMAIN = "erased.invalid"          # email = f"{pseudonym}@erased.invalid"; RFC 2606 — and refused by the mail guard
ERASED_DISPLAY = "Erased user"                  # every product surface renders this for an erased actor
ERASED_HASH_MARKER = "!erased"                  # written to users.hashed_password; verify_password() returns False for it (never raises)
EXPORT_TTL_HOURS = 72                           # == project_export_vocab.EXPORT_TTL_HOURS (asserted)
EXPORT_MAX_BYTES = 256 * 1024 * 1024            # the archive is downloaded, never uploaded: nginx's 55 MB body cap does not apply
PART_ROWS_MAX = 5_000                           # == project_export_vocab.PART_ROWS_MAX (asserted)
PART_BYTES_MAX = 8 * 1024 * 1024                # == project_export_vocab.PART_BYTES_MAX (asserted)
CURSOR_BATCH_ROWS = 1_000
JOB_STALE_MINUTES = 40                          # == project_export_vocab.JOB_STALE_MINUTES (asserted)
QUEUED_REDISPATCH_MINUTES = 10
REDISPATCH_MAX = 3                              # an EXPORT is then failed "interrupted"; an ERASURE is never failed by the janitor (§2.3 E-8)
JOBS_LIST_LIMIT = 10
PREVIEW_COUNT_TIMEOUT_MS = 5_000                # per count statement; a timed-out count reads {count: null, measured: false}
STORAGE_PREFIX_EXPORT = "subject-exports/"      # COMPLIANCE_BUCKET key: subject-exports/YYYY/MM/DD/<job_id>.zip (no user id)

# ── W38 (W31 F-1): the self-service erasure REQUEST and the administrators' queue ──
ERASURE_REQUEST_STATUSES = ("pending", "withdrawn", "declined", "accepted")   # subject_erasure_requests.status; CHECK in 0256
DECIDED_REQUEST_STATUSES = ("withdrawn", "declined", "accepted")              # decided_at is set exactly for these (ck_ser_decided)
ERASURE_REQUEST_FILTERS = ("pending", "decided", "all")                       # GET /users/erasure-requests?status=
ERASURE_REQUESTS_LIST_LIMIT = 200
DECLINE_CODES = ("legal_obligation", "active_contract", "identity_unconfirmed", "other")   # a decline carries a CODE, never free text (D-2); CHECK in 0256
DECLINE_SENTENCES = {   # the person reads the sentence of the code (MyErasureRequest.decline_message)
    "legal_obligation": "We must keep your account for a legal obligation. Ask your administrator for details.",
    "active_contract": "Your account is still needed for work in progress. Ask your administrator when it can be erased.",
    "identity_unconfirmed": "We could not confirm the request came from you. Ask your administrator.",
    "other": "Your administrator declined the request. Ask them why.",
}
BELL_SENTENCES = {      # the in-app bell rows (D-6): titles / body with no name, so nothing for an erasure to rewrite
    "erasure_request_waiting": "An erasure request is waiting for review",           # title, every OTHER active admin
    "erasure_request_waiting_body": "Review it on the Users page, Erasure requests tab.",
    "erasure_request_declined": "Your erasure request was declined",                 # title, the person (body = DECLINE_SENTENCES[code])
}
BELL_APP_PATHS = {      # delivery_metadata.app_path — exact strings in APP_PATH_RE and notificationHref.ts
    "erasure_request_waiting": "/users?tab=erasure-requests",
    "erasure_request_declined": "/settings/profile",
}

ERROR_SENTENCES = {   # detail = {"code", "message"} on every W31 4xx/5xx; a failed job's `error` uses the same text
    "job_not_found": "Subject data job not found.",                                        # 404 — another person's job alike
    "user_not_found": "User not found.",                                                   # 404 (== users.py's sentence)
    "session_required": "Sign in to do this; an API key cannot export or erase personal data.",   # 403 for every API-key principal
    "export_running": "An export of this person's data is already running.",               # 409 (one queued/running export per subject)
    "export_not_ready": "This export has not finished yet.",                               # 409 on download while queued/running
    "export_failed": "This export failed; start a new one.",                               # 409 on download of a failed job
    "export_expired": "This export was removed after 72 hours; start a new one.",          # 410 on download
    "not_an_export": "Only an export has a download.",                                     # 409 on an erasure job's download
    "archive_unreadable": "The archive could not be read from storage. Try again.",        # 502 (== W24 / retention)
    "user_erased": "This account was erased; it cannot be changed or restored.",           # 409 on every route of §3.2 and on an export of an erased user
    "not_a_person": "This is a service or synthetic account; it holds no personal data to erase.",   # 409
    "self_erasure": "You cannot erase your own account; ask another administrator.",       # 409
    "last_admin": "Cannot erase the last active administrator.",                           # 409
    "erasure_running": "An erasure of this account is already in progress.",               # 409 (a second POST while queued/running)
    "export_blocks_erasure": "An export of this person's data is still running; erase after it finishes.",   # 409
    "confirm_mismatch": "Type the username exactly as shown to confirm.",                  # 422
    "export_build": "The export could not be built ({error_type}).",
    "export_write": "The export could not be written to storage ({error_type}).",
    "export_unverified": "The stored export could not be verified ({reason}).",
    "export_too_large": "This person's export is larger than 256 MB.",
    "interrupted": "The job was interrupted; nothing was kept. Start it again.",           # export only
    "erasure_incomplete": "The erasure stopped after the {phase} step; it resumes on its own within the hour, or run it again now.",  # erasure job error (status stays "running")
    "username_reserved": 'Usernames starting with "erased-" are reserved.',                # 422 string detail on create / invite / edit / SCIM / JIT (G-C)
    "project_export_removed": "This export was removed because a person it lists was erased; export the project again.",   # a W24 project_data_jobs row's `error` (E2), error_code "subject_erased"
    # W38 (W31 F-1): the erasure request and the administrators' queue
    "request_pending": "You already asked for erasure. An administrator will review it.",        # 409 + detail.request_id (one pending request per person)
    "no_pending_request": "There is no pending erasure request.",                                 # 409 on withdraw
    "request_not_pending": "This request was already decided or withdrawn.",                      # 409 on decline
    "request_not_found": "Erasure request not found.",                                            # 404 on decline
    "own_request": "Another administrator must review your own request.",                         # 409 on decline of the caller's own request
    "decline_code_invalid": "Choose a reason from the list.",                                     # 422 (coded, not FastAPI's envelope)
    "last_admin_self": "You are the only administrator, so your account cannot be erased. Make someone else an administrator first.",   # 409 on filing
}
UI_SENTENCES = {      # G-D renders these verbatim (React text, never HTML)
    "my_data_title": "Your data",
    "my_data_body": "Download a copy of everything TestLookup holds about you: profile, memberships, preferences, sessions and key details (never a secret), sign-in links, your comments and reviews, chat history, assignments, and the activity and audit records where you are the actor.",
    "my_data_start": "Download my data",
    "my_data_restart": "Prepare a new copy",       # the Profile card's start button once an export is ready (OR-10)
    "export_queued": "Preparing your archive…",
    "export_ready": "Ready — {size}, signed. Available until {expires}.",
    "export_none": "No export yet.",
    "erase_title": "Erase this person",
    "erase_body": "Erasing replaces this person's name, username and e-mail with {pseudonym} everywhere, deletes their sessions, keys, preferences and chat history, and signs them out. Audit records keep their name, as the law allows (Art. 17(3)). This cannot be undone.",
    "erase_confirm_label": "Type {username} to confirm",
    "erase_button": "Erase permanently",
    "erase_done": "Erased. This account now reads as “Erased user” ({pseudonym}).",
    "erase_kept_title": "Kept (Art. 17(3))",
    "erased_badge": "Erased",
    # W38 F-1 (G-C renders these): the Profile request block, the queue, and the SCIM requester label.
    "erasure_request_start": "Ask to have your account erased",
    "erasure_request_confirm": "An administrator reviews the request. If they erase your account you are signed out at once and it cannot be undone; audit records are kept, as the law allows.",
    "erasure_request_send": "Send request",
    "erasure_request_pending": "You asked on {date}. An administrator will review it.",
    "erasure_request_withdraw": "Withdraw request",
    "erasure_request_declined": "Declined on {date}: {message}",
    "erasure_request_asked": "This person asked for erasure on {date}.",
    "erasure_request_accepted": "Accepted — erasure started",
    "requester_scim": "Your identity provider (SCIM)",
}
AUDIT_ACTIONS = (     # access_audit_logs.action
    "subject_export_requested", "subject_export_downloaded", "subject_erasure_requested", "subject_erased",
    # W38: the request's own rows (actor = the person / the administrator; after_value {request_id} (+ decline_code)).
    # Acceptance is the erasure's subject_erasure_requested row with after_value gaining request_id (D-5).
    "erasure_request_filed", "erasure_request_withdrawn", "erasure_request_declined",
)
