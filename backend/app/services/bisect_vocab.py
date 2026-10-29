"""The history-bisect narrowing vocabulary (W23b FAIL-12), in ONE place.

``GET /api/v1/runs/{run_id}/bisect?fingerprint=`` narrows ONE failing test of a
run to its own last pass on the same branch series (or on ``main`` / ``master``
as a labelled fallback) and, online, lists the commits between the two builds
through the EXISTING offline-gated, SSRF-guarded git-host compare. Every
enum-like field of the answer (``state``, ``anchor``, ``outcome``,
``compare.state``, ``compare.reason``) is a plain ``str`` on the wire; the values
it may take live here. The frontend mirrors them in
``frontend/src/types/bisect.ts`` (pinned by
``tests/test_bisect_drift_vocab_parity.py``).

Observe only: no bisect job, no table, no CI credential. Absence is not health
(no prior pass inside the 200-run bound is ``no_pass_within_bound``, never
"never"); a timeout is not a failure (``timed_out`` offers Retry).

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

BISECT_MAX_RUNS = 200                       # runs of the series read, newest first, the failing run included
BISECT_FALLBACK_BRANCHES = ("main", "master")   # == _pick_anchor's fallback (commit_attribution_service.py:483)
UNTESTED_LIST_MAX = 20                      # untested_runs items; untested_count is the full count
BISECT_STATES = ("narrowed", "never_passed", "no_pass_within_bound", "not_failing")
ANCHOR_CLASSES = ("same_branch", "main_fallback")         # where the good run was found
TEST_OUTCOMES = ("pass", "fail", "untested")              # per run of the walk (skipped / unknown / absent = untested)
COMPARE_STATES = ("fetched", "unavailable", "timed_out", "not_attempted")
COMPARE_REASONS = (                         # compare.reason when state != "fetched"
    # the git-host reasons, == github_target.GITHUB_UNAVAILABLE_REASONS (github_target.py:42), same words on the SPA
    "offline_mode", "github_checks_off", "no_integration", "integration_disabled", "repo_mismatch", "no_token",
    # bisect's own
    "not_narrowed", "no_good_commit", "no_bad_commit", "same_commit", "cache_unavailable", "fetch_failed", "timed_out",
)
COMPARE_BUDGET_SECONDS = 8.0                # whole compare (compare call + file fan-out); asyncio.wait_for
COMPARE_CACHE_TTL_SECONDS = 3600            # a fetched commit list between two fixed SHAs never changes
COMPARE_FAILED_TTL_SECONDS = 60             # a refused / failed compare is remembered 60 s (rate-limit guard); a TIMEOUT is not cached
COMPARE_CACHE_KEY = "testlookup:bisect:compare:{project_id}:{host}:{good}:{bad}"   # host github|gitlab; SHAs lower-cased, <= 64 chars each
CACHE_IO_TIMEOUT_SECONDS = 0.5              # a Redis get / set slower than this = cache unavailable
LISTED_COMMITS_MAX = 50                     # ranked commits returned; compare.commit_count is the fetched count
BISECT_SENTENCES = {                        # fixed copy; the SPA shows these verbatim ({...} filled by the SPA from the answer)
    "narrowed": "Last passed in build {good} ({good_sha}), first failed in build {bad} ({bad_sha}).",
    "narrowed_main": (
        "Never passed on {branch} in the runs read; last passed on {fallback} in build {good} ({good_sha}). "
        "First failed on {branch} in build {bad} ({bad_sha})."
    ),
    "never_passed": "This test never passed on {branch}: every run of it there failed ({runs} runs read).",
    "no_pass_within_bound": "No pass in the last 200 runs of {branch}; older history was not searched.",
    "not_failing": "This test did not fail in build {run}: there is nothing to narrow.",
    "untested": "{n} run(s) in between did not run this test.",
    "no_commit": "no commit recorded",
    "same_commit": (
        "The last pass and the first failure ran the same commit, so no code change explains this failure "
        "(a flaky test, data or the environment)."
    ),
    "no_good_commit": "The last passing build recorded no commit, so the commits in between cannot be listed.",
    "no_bad_commit": "The first failing build recorded no commit, so the commits in between cannot be listed.",
    "cache_unavailable": (
        "The commit list is not fetched right now (the cache is unavailable). The two builds above are exact."
    ),
    "timed_out": "The commit list did not arrive within 8 seconds.",
    "fetch_failed": "The git host refused or failed the compare; the two builds above are exact.",
    "merge_caveat": (
        "Compared as the git host's {good_sha}...{bad_sha} (commits reachable from the failure and not from "
        "the pass): with merges or a rebase this is not a straight line."
    ),
}
BISECT_ERRORS = {"fingerprint_not_in_run": "That test is not part of this run."}   # 404 detail {"code","message"}
