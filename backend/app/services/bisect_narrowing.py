"""The history-bisect walk (W23b FAIL-12), PURE: no session, no I/O.

``bisect_service.narrow_range_for_test`` reads ONE bounded statement -- the
newest ``BISECT_MAX_RUNS`` runs of the failing run's branch series (the run
itself first) with this test's status in each, plus the newest passes of the
test on ``main`` / ``master`` -- and hands the rows here. ``walk`` judges them:

* the run's own outcome is not a failure -> ``not_failing`` (nothing to narrow);
* otherwise the failing streak is followed back: every older failure moves
  ``bad`` (the OLDEST failure of the streak) one run back; a run that did not
  run the test (no row, ``SKIPPED`` or ``UNKNOWN``) is "untested"; the first
  pass ends the walk and is ``good``. Untested runs INSIDE the streak are after
  ``bad`` and are dropped; the ones between ``good`` and ``bad`` are kept,
  newest first. A pass without a commit ends the walk too (the compare then
  says ``no_good_commit``): an older pass is never substituted, because that
  would widen the range silently;
* no pass in the series -> the newest ``main`` / ``master`` pass OLDER than
  ``bad`` (the ``_pick_anchor`` fallback of ``commit_attribution_service``),
  labelled ``main_fallback``; a run ON ``main`` / ``master`` has no fallback;
* still none -> ``no_pass_within_bound`` when the bound was hit (older history
  was not read, so never "never"), else ``never_passed``.

``compare_gate`` says whether the git-host compare may be attempted at all.
The vocabulary lives in ``bisect_vocab``.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from app.services.bisect_vocab import BISECT_FALLBACK_BRANCHES, BISECT_MAX_RUNS, UNTESTED_LIST_MAX
from app.services.run_compare_service import _status_bucket

_PASS, _FAIL, _UNTESTED = "pass", "fail", "untested"


@dataclass(frozen=True)
class WalkRow:
    """One run of the walk, as plain values (the statement's row)."""

    run_id: uuid.UUID
    build_number: str
    commit_hash: Optional[str]
    branch: Optional[str]
    created_at: datetime
    ci_run_url: Optional[str]
    outcome: str  # TEST_OUTCOMES: this test in that run


@dataclass(frozen=True)
class Narrowing:
    """The walk's verdict. ``untested`` is capped at ``UNTESTED_LIST_MAX`` (newest
    first); ``untested_count`` is the full count (a top-N length is not a count)."""

    state: str
    anchor: Optional[str]
    fallback_branch: Optional[str]
    good: Optional[WalkRow]
    bad: Optional[WalkRow]
    streak_length: int
    untested: tuple[WalkRow, ...]
    untested_count: int
    runs_read: int
    bounded: bool


def series_key(branch: Optional[str]) -> str:
    """The series a run belongs to: its branch, case-insensitive; ``''`` for no branch.

    The twin of the index expression ``coalesce(lower(branch), '')``."""
    return (branch or "").lower()


def outcome_of(status: Optional[str]) -> str:
    """This test's outcome in one run: passed -> pass; failed / broken -> fail; else untested.

    ``None`` (no row in that run), ``SKIPPED`` and ``UNKNOWN`` are all untested."""
    bucket = _status_bucket(status)
    if bucket == "passed":
        return _PASS
    if bucket in ("failed", "broken"):
        return _FAIL
    return _UNTESTED


def _verdict(
    state: str,
    *,
    runs_read: int,
    bounded: bool,
    good: Optional[WalkRow] = None,
    bad: Optional[WalkRow] = None,
    anchor: Optional[str] = None,
    fallback_branch: Optional[str] = None,
    streak_length: int = 0,
    untested: Sequence[WalkRow] = (),
) -> Narrowing:
    rows = tuple(untested)
    return Narrowing(
        state=state, anchor=anchor, fallback_branch=fallback_branch, good=good, bad=bad,
        streak_length=streak_length, untested=rows[:UNTESTED_LIST_MAX], untested_count=len(rows),
        runs_read=runs_read, bounded=bounded,
    )


def walk(
    series: Sequence[WalkRow], fallback: Sequence[WalkRow], *, bound: int = BISECT_MAX_RUNS,
) -> Narrowing:
    """Judge the rows of one statement (contract §2.1 B-2).

    ``series``: the series' runs newest first, the failing run itself first, at
    most ``bound`` (extra rows are ignored). ``fallback``: the test's passes on
    ``main`` / ``master``, newest first (empty for a run on those branches)."""
    rows = list(series)[:bound]
    runs_read = len(rows)
    bounded = runs_read >= bound
    if not rows or rows[0].outcome != _FAIL:
        return _verdict("not_failing", runs_read=runs_read, bounded=bounded)

    bad = rows[0]
    streak_length = 1
    pending: list[WalkRow] = []
    for row in rows[1:]:
        if row.outcome == _FAIL:
            # An older failure of the same streak: the untested runs seen so far
            # are INSIDE the streak (after bad), not between the pass and it.
            bad, streak_length, pending = row, streak_length + 1, []
        elif row.outcome == _PASS:
            return _verdict(
                "narrowed", runs_read=runs_read, bounded=bounded, good=row, bad=bad,
                anchor="same_branch", streak_length=streak_length, untested=pending,
            )
        else:
            pending.append(row)

    if series_key(rows[0].branch) not in BISECT_FALLBACK_BRANCHES:
        for row in fallback:  # newest first: the first older pass is the newest one
            if row.outcome == _PASS and row.created_at < bad.created_at:
                return _verdict(
                    "narrowed", runs_read=runs_read, bounded=bounded, good=row, bad=bad,
                    anchor="main_fallback", fallback_branch=series_key(row.branch),
                    streak_length=streak_length,
                )

    state = "no_pass_within_bound" if bounded else "never_passed"
    return _verdict(state, runs_read=runs_read, bounded=bounded, bad=bad, streak_length=streak_length)


def compare_gate(narrowing: Narrowing) -> Optional[str]:
    """``None`` = the compare may be attempted; else the ``compare.reason`` that says why not.

    Decided before any offline / git-host check, so "same commit" is said offline too."""
    good, bad = narrowing.good, narrowing.bad
    if narrowing.state != "narrowed" or good is None or bad is None:
        return "not_narrowed"
    good_sha = (good.commit_hash or "").strip()
    bad_sha = (bad.commit_hash or "").strip()
    if not good_sha:
        return "no_good_commit"
    if not bad_sha:
        return "no_bad_commit"
    if good_sha.lower() == bad_sha.lower():
        return "same_commit"
    return None
