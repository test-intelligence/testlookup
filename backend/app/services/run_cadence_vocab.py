"""The expected-run-cadence vocabulary (W23a TRND-03), in ONE place.

A cadence is TestLookup's own EXPECTATION ("a run of <suite | any> arrives by
<time>"); nothing here reads or writes CI. Every enum-like field (``mode``,
``outcome``) is a plain ``str`` on the wire and in the database; the values it
may take live here as module constants. The frontend mirrors them in
``frontend/src/types/runCadence.ts`` (pinned by
``tests/test_insight_cadence_vocab_frontend_parity.py``); ``CADENCE_MODES`` and
``WINDOW_OUTCOMES`` are also the CHECK literal lists of migration ``0244`` and
of the ORM (same test).

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

CADENCE_MODES = ("weekly", "cron")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # bit i = WEEKDAYS[i] (Python weekday()); mask 1..127
WINDOW_OUTCOMES = ("met", "missed", "paused")       # run_cadence_windows.outcome (what the beat DECIDED)
STATUS_OUTCOMES = ("met", "missed", "paused", "pending")   # /status adds "pending" (deadline not reached; never stored)
GRACE_DEFAULT, GRACE_MIN, GRACE_MAX = 60, 0, 720    # minutes
PAUSE_MAX_DAYS = 90
PAUSE_REASON_MAX = 200
MAX_CADENCES_PER_PROJECT = 20
MAX_OCCURRENCES_PER_DAY = 24                        # a cron expanding to more is refused
CHECK_LOOKBACK_DAYS = 7                             # the beat never decides a deadline older than this
CHECK_MAX_WINDOWS_PER_TICK = 200                    # per cadence per tick (catch-up bound)
STATUS_DAYS = (1, 7, 14, 30, 60, 90)                # /status ?days= (the Trends / Coverage windows)
MISSED_LIST_MAX = 50                                # missed_windows items; `missed` (the count) is never capped
CADENCE_CHECK_LOCK_KEY = "testlookup:lock:run-cadence-check"
CADENCE_CHECK_LOCK_TTL_SECONDS = 840                # < the 15-min cadence
RUN_MISSED_EVENT = "run.missed"                     # == NotificationEventType.RUN_MISSED.value (parity test)
NO_CADENCE_SENTENCE = "No expected cadence configured"
CONFIGURE_LABEL = "Configure"

#: detail = {"code": <key>, "message": <sentence>} on every 4xx of the cadence routes.
CADENCE_ERRORS = {
    "cadence_not_found": "Expected cadence not found.",                                      # 404
    "duplicate_scope": "This project already has an expected cadence for that suite.",       # 409 (suite_name null: "…for any run.")
    "too_many": "A project can have at most 20 expected cadences.",                          # 409
    "unknown_mode": "Unknown cadence mode. Valid: weekly, cron.",                            # 422
    "weekly_fields": "A weekly cadence needs at least one weekday and an expected-by time.", # 422
    "cron_fields": "A cron cadence needs a cron expression and no weekdays or time.",        # 422
    "bad_cron": "That cron expression could not be read. Use five fields: minute hour * * day-of-week.",  # 422
    "cron_day_fields": "Only minute, hour and day-of-week are supported; day-of-month and month must be *.",  # 422
    "cron_too_frequent": "That cron expression expects more than 24 runs a day.",            # 422
    "unknown_timezone": "Unknown time zone. Use an IANA name such as Europe/Berlin.",        # 422
    "pause_until_past": "The pause must end in the future.",                                 # 422
    "pause_too_long": "A pause can last at most 90 days.",                                   # 422
    "pause_reason_required": "Say why the expectation is paused.",                           # 422
    "not_paused": "This expected cadence is not paused.",                                    # 409 (resume)
    "suite_conflict": "Send suite_name or clear_suite, not both.",                           # 422 (PATCH)
}
