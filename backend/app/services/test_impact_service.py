"""STRAT-08 — "Likely affected tests" for a run (advisory, read-only).

For one run, rank THIS run's tests by how likely the run's changes touched
them, from two pieces of evidence the product already stores:

* **Path overlap** — ``commit_attribution_service.path_overlap`` between each
  changed file of the run's commit range (``run_commit_ranges.commits[].files``)
  and the test's derived location (``build_locator([test_case])``: the stack
  trace's repo-relative path, class / package tokens, the test-name stem).
* **Past co-failures** — in how many PRIOR runs of the SAME project, inside the
  readiness window, whose range touched at least one of these exact paths, this
  test was FAILED / BROKEN. Only when ``get_tia_readiness`` says the project's
  corpus is ready; otherwise ``co_failure_runs`` is ``None`` (not measured),
  never ``0`` (measured, none).

What this is NOT: a test-selection list. Nothing here — and nothing that reads
it — chooses, skips, quarantines or gates a test; every answer restates
``ADVISORY_CAVEAT``. It is a ranking a person reads.

Honesty: a run without a commit range answers ``available: false`` +
``no_commit_range``; a range without changed files (``unavailable`` source, or
every commit's ``files`` empty) answers ``no_changed_files``. An available
answer with no scoring test is ``tests: []`` — a measured "nothing matched",
never a stand-in for "could not tell".

Read-only: SELECTs only (no add / flush / commit), never
``lazy_resolve_commit_range``, and every query is scoped by the run's
``project_id`` — a fingerprint is md5(test_name:class_name), not unique across
projects.
"""
from __future__ import annotations

import bisect
import heapq
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Iterable, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.postgres import FlakyScore, RunCommitRange, TestCase, TestRun
from app.services.commit_attribution_service import (
    BASE_SOURCE_UNAVAILABLE,
    BASE_SOURCES,
    SOURCE_UNAVAILABLE,
    STRONG_BASE_SOURCES,
    TIA_READINESS_SCAN_CAP,
    TIA_READINESS_WINDOW_DAYS,
    _FailingLocator,
    _path_segments,
    _strip_test_affixes,
    build_locator,
    get_tia_readiness,
    path_overlap,
)
from app.services.failure_attribution_service import (
    FLAKY_SCORE_FLOOR,
    WEAK_CONFIDENCES,
    _clamp,
)

# ── Published constants ───────────────────────────────────────────────────────

# Score = W_OVERLAP · overlap + W_COFAIL · min(1, co_failure_runs / COFAIL_SATURATION)
# when the project's history is ready; score = overlap when it is not. Overlap
# leads: it is evidence from THIS run's own changed files, while a co-failure
# count is correlation over past runs.
W_OVERLAP = 0.6
W_COFAIL = 0.4
# Five past co-failures with these paths is full co-failure credit — beyond
# that more runs say the same thing.
COFAIL_SATURATION = 5
# Changed paths scored per run (de-duplicated, in commit order oldest → newest).
# A range can carry 100 commits × 500 files; past this the ranking adds cost,
# not signal. ``changed_files_count`` still reports the real number.
MAX_CHANGED_PATHS = 2000
# Matched paths named per test (strongest first).
MAX_REASON_PATHS = 5
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
# ``path_overlap``'s fuzzy-stem rule only fires for a changed-file stem of at
# least this length (``len(cf_stem) >= 3``); the prefilter mirrors it.
FUZZY_MIN_STEM = 3
# A module token carried by at least this share of the run's candidate tests
# cannot tell one test from another — the org token ``acme`` of every
# ``com.acme.*`` class is a segment of every changed Java path, so rule B gave
# every test >= 0.7 against every file. Such a token is dropped from rules B and
# D (the token atoms) for that run's answer; stems (A) and dir sets (C) stay.
# Half: a token most tests share describes the suite, not any one test.
UNINFORMATIVE_TOKEN_SHARE = 0.5
# With fewer candidate tests "half of them" is one or two tests — a token two of
# three tests share still separates them from the third — so below this the rule
# is not applied and every token counts.
MIN_TESTS_FOR_TOKEN_FILTER = 4

ADVISORY_CAVEAT = (
    "Advisory only: ranked by path overlap and past co-failures. "
    "It never selects or skips tests."
)
REASON_NO_COMMIT_RANGE = "no_commit_range"
REASON_NO_CHANGED_FILES = "no_changed_files"
# Same vocabulary as failure_attribution_service.attribute_run's failing read.
FAILED_STATUSES = ("FAILED", "BROKEN")


# ── Pure helpers ──────────────────────────────────────────────────────────────


def changed_paths(commits: Any) -> list[str]:
    """Every changed path of a stored range, de-duplicated in commit order.

    A file touched by three commits is one piece of evidence, not three (the
    same rule as ``failure_attribution_service.attribute_run``).
    """
    seen: dict[str, None] = {}
    for commit in commits or []:
        if not isinstance(commit, dict):
            continue
        for path in commit.get("files") or []:
            if isinstance(path, str) and path:
                seen.setdefault(path, None)
    return list(seen)


def file_detail_coverage(commits: Any) -> Optional[float]:
    """Share of this range's commits that carry a non-empty ``files`` list.

    ``None`` when the range has no commits (nothing to measure) — the same
    per-commit definition as ``compute_tia_readiness``'s corpus-wide figure.
    """
    dicts = [c for c in (commits or []) if isinstance(c, dict)]
    if not dicts:
        return None
    with_files = sum(1 for c in dicts if c.get("files"))
    return round(with_files / len(dicts), 4)


def is_known_flaky(score: Any, confidence: Any) -> bool:
    """The flaky rule of ``failure_attribution_service.compose``, verbatim.

    A flaky score argues for flakiness only when it clears
    ``FLAKY_SCORE_FLOOR`` AND rests on more than a ``none`` / ``low``
    confidence band — a thin-history score must not sneak back in as a signal.
    """
    clamped = _clamp(score) if score is not None else 0.0
    return clamped >= FLAKY_SCORE_FLOOR and (confidence or "none") not in WEAK_CONFIDENCES


def _status_text(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value) if value else "UNKNOWN"


# ── Prefilter: which changed files can a test overlap at all? ─────────────────
#
# ``path_overlap(loc, f) > 0`` holds exactly when one of its four rules fires:
#   A. f's subject stem is one of ``loc.stems``                      (1.0)
#   B. a ``loc.module_tokens`` token is one of f's path segments      (0.7)
#   C. a ``loc.dir_sets`` directory is one of f's directories       (<= 0.8)
#   D. f's stem has >= 3 chars and contains, or is contained in, a
#      module token                                                   (0.6)
# The index below answers each rule by lookup instead of comparing every test
# with every file: A by stem, B and C by segment (f's directories are a subset
# of its segments, so C needs no index of its own), D by substring search over
# the stems. It may return a file the exact rule then scores 0 (C's noise-segment
# filter, for one) — never the reverse; the exact ``path_overlap`` decides.
#
# Each rule is a max over independent atoms of the locator — one stem (A), one
# module token (B, D), one dir set (C) — and ``round`` is monotone, so
# ``path_overlap(loc, f) == max(path_overlap(one-atom locator, f))`` over loc's
# atoms. The scores are therefore computed ONCE per distinct atom and file and
# shared by every test carrying that atom (per-test calls were 600,000 at 2,000
# tests x 300 files when one token matched every path). Dropping the run's
# uninformative tokens (``uninformative_tokens``) removes atoms, nothing else.
# ``tests/test_test_impact_service.py`` compares the result with the literal
# ``path_overlap`` of the (token-filtered) locator on random and edge inputs.


class _ChangedFileIndex:
    """Inverted indexes over the changed files (built once per answer)."""

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = list(paths)
        self.by_stem: dict[str, list[int]] = {}
        self.by_segment: dict[str, list[int]] = {}
        self._long: dict[str, list[int]] = {}  # stems of >= FUZZY_MIN_STEM chars (rule D)
        for i, path in enumerate(self.paths):
            segs = _path_segments(path)
            if not segs:
                continue  # path_overlap scores a segment-less path 0
            stem = _strip_test_affixes(segs[-1])
            if stem:
                self.by_stem.setdefault(stem, []).append(i)
                if len(stem) >= FUZZY_MIN_STEM:
                    self._long.setdefault(stem, []).append(i)
            for seg in set(segs):
                self.by_segment.setdefault(seg, []).append(i)
        # Rule D's "token inside a stem": one C-level search over the stems
        # joined by NUL instead of a Python loop per (token, stem) pair.
        self._stems = list(self._long)
        self._blob = "\x00".join(self._stems)
        self._starts: list[int] = []
        pos = 0
        for stem in self._stems:
            self._starts.append(pos)
            pos += len(stem) + 1
        self._fuzzy_cache: dict[str, frozenset[int]] = {}
        self._atom_cache: dict[tuple[str, Any], dict[int, float]] = {}

    def _fuzzy(self, token: str) -> frozenset[int]:
        """Files whose long stem contains ``token`` or is contained in it (rule D)."""
        cached = self._fuzzy_cache.get(token)
        if cached is not None:
            return cached
        hits: set[str] = set()
        if self._stems:
            if "\x00" in token:  # cannot use the joined blob; compare directly
                hits.update(s for s in self._stems if token in s)
            else:
                at = self._blob.find(token)
                while at != -1:
                    k = bisect.bisect_right(self._starts, at) - 1
                    hits.add(self._stems[k])
                    if k + 1 >= len(self._starts):
                        break
                    at = self._blob.find(token, self._starts[k + 1])
            n = len(token)
            if n >= FUZZY_MIN_STEM:
                # A stem inside the token: enumerate the token's substrings when
                # that is cheaper than scanning every stem.
                if (n - 1) * (n - 2) // 2 <= len(self._stems):
                    for i in range(n):
                        for j in range(i + FUZZY_MIN_STEM, n + 1):
                            if token[i:j] in self._long:
                                hits.add(token[i:j])
                else:
                    hits.update(s for s in self._stems if s in token)
        files = frozenset(i for stem in hits for i in self._long[stem])
        self._fuzzy_cache[token] = files
        return files

    def _atom(self, kind: str, key: Any) -> dict[int, float]:
        """``{file: path_overlap(one-atom locator, file)}`` for the files it scores > 0 (cached).

        An atom is one stem (rule A), one module token (B, D) or one dir set (C);
        the prefilter picks its candidate files and ``path_overlap`` scores them.
        """
        cached = self._atom_cache.get((kind, key))
        if cached is not None:
            return cached
        loc = _FailingLocator()
        files: Iterable[int]
        if kind == "stem":
            loc.stems = {key}
            files = self.by_stem.get(key, ())
        elif kind == "token":
            loc.module_tokens = {key}
            files = set(self.by_segment.get(key, ())) | self._fuzzy(key)
        else:  # "dirs": a frozenset of directory names
            loc.dir_sets = [set(key)]
            files = {i for name in key for i in self.by_segment.get(name, ())}
        scored: dict[int, float] = {}
        for i in files:
            value = path_overlap(loc, self.paths[i])
            if value > 0:
                scored[i] = value
        self._atom_cache[(kind, key)] = scored
        return scored

    def overlaps(self, loc: _FailingLocator, drop: Collection[str] = frozenset()) -> dict[int, float]:
        """``{file: path_overlap(loc', file)}`` for every file scoring > 0: the max over the atoms of
        ``loc'`` = ``loc`` without the module tokens in ``drop`` (stems and dir sets unchanged)."""
        atoms: list[tuple[str, Any]] = [("stem", s) for s in loc.stems]
        atoms += [("token", t) for t in loc.module_tokens if t not in drop]
        atoms += [("dirs", frozenset(d)) for d in loc.dir_sets if d]
        out: dict[int, float] = {}
        for kind, key in atoms:
            for i, value in self._atom(kind, key).items():
                if value > out.get(i, 0.0):
                    out[i] = value
        return out


def overlap_for(
    index: _ChangedFileIndex, loc: _FailingLocator, drop: Collection[str] = frozenset(),
) -> tuple[float, list[tuple[float, str]]]:
    """``(max overlap, [(overlap, path), …])`` — equal to ``path_overlap(loc', f)`` for every changed
    ``f``, where ``loc'`` is ``loc`` with the module tokens in ``drop`` removed."""
    hits = [(value, index.paths[i]) for i, value in index.overlaps(loc, drop).items()]
    best = max((value for value, _path in hits), default=0.0)
    return best, hits


def uninformative_tokens(locators: Sequence[_FailingLocator]) -> frozenset[str]:
    """Module tokens carried by >= ``UNINFORMATIVE_TOKEN_SHARE`` of the run's candidate tests.

    Computed once per answer over THIS run's candidates; empty below
    ``MIN_TESTS_FOR_TOKEN_FILTER`` tests. Each test counts a token once.
    """
    total = len(locators)
    if total < MIN_TESTS_FOR_TOKEN_FILTER:
        return frozenset()
    counts = Counter(token for loc in locators for token in loc.module_tokens)
    return frozenset(token for token, n in counts.items() if n >= UNINFORMATIVE_TOKEN_SHARE * total)


# ── The ranker (pure) ─────────────────────────────────────────────────────────


def rank_affected_tests(
    changed_files: Iterable[str],
    tests: Sequence[Any],
    cofail: Mapping[str, int],
    *,
    history_available: bool,
    limit: int = DEFAULT_LIMIT,
    history_runs_considered: Optional[int] = None,
    known_flaky: Collection[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Rank ``tests`` by likely impact of ``changed_files``. Pure, deterministic.

    * overlap = max ``path_overlap(loc', f)`` over the first ``MAX_CHANGED_PATHS``
      de-duplicated changed files, where ``loc'`` is ``build_locator([test])``
      without the run's ``uninformative_tokens`` (module tokens >= half of
      ``tests`` carry, when there are >= ``MIN_TESTS_FOR_TOKEN_FILTER`` tests;
      stems and dir sets kept). Prefiltered, see ``_ChangedFileIndex``; the
      prefilter never drops a scoring file.
    * ``history_available``: score = ``W_OVERLAP·overlap + W_COFAIL·min(1,
      co_failure_runs / COFAIL_SATURATION)``; ``co_failure_runs`` is
      ``cofail[fingerprint]`` (0 when absent: measured, none).
      Not available: score = overlap and ``co_failure_runs`` /
      ``history_runs_considered`` are ``None`` — not measured, never 0.
    * Known flaky (``known_flaky``, the ``is_known_flaky`` rule): the test is
      never dropped for it and keeps its path overlap, but its co-failure count
      earns no score — the precedent (``failure_attribution_service.compose``)
      lets a qualified flaky history stop co-occurrence evidence from becoming
      a conclusion ("signals disagree" → uncertain) while never hiding the
      test. A flaky test failing in runs that touched these paths is what its
      flakiness alone predicts. The measured count is still reported, with
      ``known_flaky: true`` beside it.
    * Only score > 0; order score desc, then fingerprint asc; at most ``limit``.
    """
    paths = list(dict.fromkeys(p for p in changed_files if isinstance(p, str) and p))[:MAX_CHANGED_PATHS]
    index = _ChangedFileIndex(paths)
    flaky = set(known_flaky)
    locators = [build_locator([tc]) for tc in tests]
    drop = uninformative_tokens(locators)
    ranked: list[dict[str, Any]] = []
    for tc, loc in zip(tests, locators):
        fingerprint = str(getattr(tc, "test_fingerprint", "") or "")
        overlap, hits = overlap_for(index, loc, drop)
        is_flaky = fingerprint in flaky
        co_failure_runs: Optional[int] = None
        considered: Optional[int] = None
        if history_available:
            co_failure_runs = max(0, int(cofail.get(fingerprint, 0) or 0))
            considered = history_runs_considered
            credited = 0 if is_flaky else co_failure_runs
            score = W_OVERLAP * overlap + W_COFAIL * min(1.0, credited / COFAIL_SATURATION)
        else:
            score = overlap
        score = round(score, 4)
        if score <= 0:
            continue
        strongest = heapq.nsmallest(MAX_REASON_PATHS, hits, key=lambda hit: (-hit[0], hit[1]))
        ranked.append({
            "test_fingerprint": fingerprint,
            "test_name": str(getattr(tc, "test_name", "") or ""),
            "class_name": getattr(tc, "class_name", None) or None,
            "status_in_run": _status_text(getattr(tc, "status", None)),
            "score": score,
            "reason": {
                "overlapping_paths": [path for _value, path in strongest],
                "overlap_score": round(overlap, 4),
                "co_failure_runs": co_failure_runs,
                "history_runs_considered": considered,
                "known_flaky": is_flaky,
            },
        })
    ranked.sort(key=lambda row: (-row["score"], row["test_fingerprint"]))
    return ranked[: max(0, int(limit))]


def _shares_a_path(commits: Any, changed: set[str]) -> bool:
    """Whether a stored range touched at least one of ``changed`` (exact path)."""
    for commit in commits or []:
        if not isinstance(commit, dict):
            continue
        for path in commit.get("files") or []:
            if isinstance(path, str) and path in changed:
                return True
    return False


# ── The read (DB) ─────────────────────────────────────────────────────────────


async def _prior_matching_runs(
    db: AsyncSession, run: TestRun, changed: set[str], *, days: int,
) -> tuple[list[Any], int, bool]:
    """PRIOR runs of the run's project, inside the readiness window, whose range
    shares >= 1 exact changed path. ONE bounded read, newest first, capped like
    the readiness scan (``TIA_READINESS_SCAN_CAP``).

    Returns ``(matching run ids, ranges read, scan capped)``. "Prior" = the
    run was created before this one (the precedent's "previous run" order).
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(RunCommitRange.run_id, RunCommitRange.source, RunCommitRange.commits)
        .join(TestRun, TestRun.id == RunCommitRange.run_id)
        .where(
            RunCommitRange.project_id == run.project_id,
            TestRun.project_id == run.project_id,
            RunCommitRange.resolved_at >= since,
            RunCommitRange.run_id != run.id,
        )
        .order_by(RunCommitRange.resolved_at.desc())
        .limit(TIA_READINESS_SCAN_CAP)
    )
    if run.created_at is not None:
        stmt = stmt.where(TestRun.created_at < run.created_at)
    rows = (await db.execute(stmt)).all()
    matching = [
        row.run_id for row in rows
        if row.source != SOURCE_UNAVAILABLE and _shares_a_path(row.commits, changed)
    ]
    return matching, len(rows), len(rows) >= TIA_READINESS_SCAN_CAP


async def _co_failure_counts(db: AsyncSession, run: TestRun, run_ids: list[Any]) -> dict[str, int]:
    """Per fingerprint of THIS run's tests: in how many of ``run_ids`` it was
    FAILED / BROKEN. ONE grouped read; the self-join on this run's rows keeps
    it to the candidates without a per-test query or a fingerprint IN-list."""
    if not run_ids:
        return {}
    current = aliased(TestCase)
    rows = (await db.execute(
        select(TestCase.test_fingerprint, func.count(func.distinct(TestCase.test_run_id)))
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .join(current, (current.test_fingerprint == TestCase.test_fingerprint) & (current.test_run_id == run.id))
        .where(
            TestCase.test_run_id.in_(run_ids),
            TestRun.project_id == run.project_id,
            TestCase.status.in_(FAILED_STATUSES),
        )
        .group_by(TestCase.test_fingerprint)
    )).all()
    return {str(fingerprint): int(count) for fingerprint, count in rows}


async def _known_flaky(db: AsyncSession, run: TestRun) -> set[str]:
    """Fingerprints of THIS run's tests whose project flaky score qualifies
    (``is_known_flaky``). Project-scoped like the precedent's FlakyScore read."""
    current = aliased(TestCase)
    rows = (await db.execute(
        select(FlakyScore.test_fingerprint, FlakyScore.score, FlakyScore.confidence)
        .join(current, (current.test_fingerprint == FlakyScore.test_fingerprint) & (current.test_run_id == run.id))
        .where(FlakyScore.project_id == run.project_id)
    )).all()
    return {str(fp) for fp, score, confidence in rows if is_known_flaky(score, confidence)}


async def get_likely_affected_tests(
    db: AsyncSession,
    run: TestRun,
    *,
    days: int = TIA_READINESS_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """The advisory "Likely affected tests" answer for one run (read-only).

    Reads: the run's range; the project's TIA readiness (``get_tia_readiness``,
    its own bounded scan); this run's test cases; and — only when readiness is
    available — the prior ranges, the co-failure counts and nothing else per
    test. The project's flaky scores for this run's tests are read either way.
    """
    range_row = (await db.execute(
        select(RunCommitRange.source, RunCommitRange.base_source, RunCommitRange.commits)
        .where(RunCommitRange.run_id == run.id, RunCommitRange.project_id == run.project_id)
    )).first()
    readiness = await get_tia_readiness(db, run.project_id, days=days)
    history: dict[str, Any] = {
        "available": bool(readiness.get("available")),
        "insufficient_data_reason": readiness.get("insufficient_data_reason"),
        "usable_runs": int(readiness.get("usable_runs") or 0),
        "window_days": days,
        "runs_considered": 0,
        "scan_capped": False,
    }
    answer: dict[str, Any] = {
        "run_id": str(run.id),
        "project_id": str(run.project_id),
        "available": False,
        "reason": REASON_NO_COMMIT_RANGE,
        "advisory": True,
        "caveat": ADVISORY_CAVEAT,
        "changed_files_count": 0,
        "file_detail_coverage": None,
        "source": None,
        "base_source": None,
        "base_anchor_is_strong": None,
        "history": history,
        "tests": [],
        "total_candidates": 0,
    }
    paths: list[str] = []
    if range_row is not None:
        # Readiness's normalisation and strong-anchor rule, unchanged.
        base_source = range_row.base_source if range_row.base_source in BASE_SOURCES else BASE_SOURCE_UNAVAILABLE
        commits = range_row.commits or []
        if range_row.source != SOURCE_UNAVAILABLE:
            paths = changed_paths(commits)
        answer.update(
            reason=REASON_NO_CHANGED_FILES,
            changed_files_count=len(paths),
            file_detail_coverage=file_detail_coverage(commits),
            source=range_row.source,
            base_source=base_source,
            base_anchor_is_strong=base_source in STRONG_BASE_SOURCES,
        )
    if not paths:
        # Gated: say how many tests the run has, so a 0 is never implied.
        answer["total_candidates"] = int((await db.execute(
            select(func.count(TestCase.id))
            .join(TestRun, TestRun.id == TestCase.test_run_id)
            .where(TestCase.test_run_id == run.id, TestRun.project_id == run.project_id)
        )).scalar_one() or 0)
        return answer

    tests = (await db.execute(
        select(
            TestCase.test_fingerprint, TestCase.test_name, TestCase.class_name, TestCase.package_name,
            TestCase.status, TestCase.stack_trace, TestCase.error_message,
        )
        .join(TestRun, TestRun.id == TestCase.test_run_id)
        .where(TestCase.test_run_id == run.id, TestRun.project_id == run.project_id)
    )).all()
    cofail: dict[str, int] = {}
    considered: Optional[int] = None
    if history["available"]:
        matching, read, capped = await _prior_matching_runs(db, run, set(paths), days=days)
        history.update(runs_considered=read, scan_capped=capped)
        considered = len(matching)
        cofail = await _co_failure_counts(db, run, matching)
    flaky = await _known_flaky(db, run) if tests else set()
    answer.update(
        available=True,
        reason=None,
        tests=rank_affected_tests(
            paths, tests, cofail,
            history_available=history["available"],
            limit=max(1, min(int(limit), MAX_LIMIT)),
            history_runs_considered=considered,
            known_flaky=flaky,
        ),
        total_candidates=len(tests),
    )
    return answer
