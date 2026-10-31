"""The suite-membership parity vocabulary (W25a HYG-04 phase 1), in ONE place.

The parity harness compares the legacy suite memberships (``suite_memberships``)
with the canonical model (``canonical_test_cases`` + ``test_suites``) once a day
and records one ``suite_membership_parity_runs`` row per comparison. Every
enum-like field is a plain ``str`` on the wire; the values it may take live
here. The frontend mirrors them in ``frontend/src/types/suiteParity.ts``
(pinned by ``tests/test_suite_parity_vocab_parity.py``); ``RUN_STATUSES`` /
``RUN_TRIGGERS`` are also the CHECK literal lists of migration ``0248`` (pinned
by ``tests/test_migration_0248.py``).

Every key of a project's comparison lands in exactly ONE reason. BLOCKING
reasons count as "differences" and break the streak; EXPECTED reasons are the
canonical model's documented semantics and are reported, never counted against
the cutover. A reason is never relabelled to reach zero (D26).

Constants only: no ``app.*`` import, no session, no I/O
(``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

RUN_STATUSES = ("running", "clean", "diffs", "failed")   # CHECK in 0248 uses the same literals
RUN_TRIGGERS = ("beat", "admin")                         # CHECK in 0248

BLOCKING_REASONS = (
    "missing_in_canonical",   # legacy knows the fingerprint in this project; canonical has no row for it at all
    "missing_in_legacy",      # canonical says suite S holds it (source 'execution'); legacy never saw it in S or elsewhere
    "retired_but_seen",       # legacy active, canonical deleted/needs_review, yet the fingerprint ran in the window
    "retire_pending",         # legacy removed, canonical still active, and the fingerprint did NOT run in the window
    "name_drift",             # same suite, same last sighting, different test_name / class_name
    "first_seen_differs",     # same suite, legacy holds the fp in ONE suite, both first_seen non-null and different
)
EXPECTED_REASONS = (
    "deletion_window",        # legacy removed on a single-run absence; canonical keeps it until absent from the window
    "legacy_stale",           # legacy active only because suite S stopped running; canonical retired it by the window
    "moved_suite",            # canonical holds the fp in another suite (one suite per test; a person's move is kept)
    "default_suite",          # canonical filed a NULL-suite case under the default suite; legacy skips NULL suites
    "catalog_only",           # canonical row with source 'managed' / 'linked' and no legacy row
    "name_stale",             # names differ but the two sides last saw the test in different runs
    "first_seen_multi_suite", # first_seen differs where legacy holds the fp in more than one suite
)
INFO_COUNTS = ("legacy_modified_events", "legacy_restored_events")  # not derivable from canonical after the cutover

# The per-key states a sample row shows (SuiteParitySampleRow.legacy / .canonical).
LEGACY_STATES = ("active", "removed", "absent")
CANONICAL_STATES = ("active", "retired", "other_suite", "absent")

SAMPLE_PER_REASON = 20       # sample rows kept per reason on a run (≤ 13 × 20 = 260 rows)
PER_PROJECT_MAX = 200        # projects listed on a run (those with ≥ 1 blocking or expected key), most blocking first
REQUIRED_CLEAN_DAYS = 7      # D26
HISTORY_DAYS = 14            # the history GET returns runs started in the last 14 UTC days
RUN_STALE_MINUTES = 60       # a 'running' row older than this is failed 'interrupted' by the next claim

INTERRUPTED_ERROR_CODE = "interrupted"   # error_code of a stale 'running' row recovered by the next claim

TASK_NAME = "run_suite_membership_parity"   # the POST's 202 body names it
NOT_FOUND_SENTENCE = "Parity run not found."
ALREADY_RUNNING_SENTENCE = "A comparison is already running."
DISPATCH_FAILED_SENTENCE = "Could not queue the comparison."
