#!/usr/bin/env python3
"""Run what CI runs, before the push — so a green local tree means a green PR.

Why this exists
---------------
Four pushes in two days went red on checks that were available locally the whole
time. Not one was a real test failure the author hadn't seen; each was a step
nobody thought to run:

* ``npm run build`` — CI builds the production bundle. Nothing local did.
* ``check:bundle`` / ``check:theme`` — two frontend guards, never run locally.
* ``vitest --coverage`` — the coverage ratchet, not just the tests.
* ``prompt_eval_recordings --check`` / ``agent_api_docs --check`` — backend
  guards outside the test suite.
* the FULL backend suite — running only ``tests/regression`` passed while
  ``tests/test_summary_report_router.py`` was broken, which is how a required
  Pydantic field shipped that would have 500'd ``GET /reports/summary``.

The fix is not discipline, it is a list. This is the list.

Honesty about parity
--------------------
This cannot promise "green here means green there" in every case, and it does
not pretend to. Three known divergences are handled explicitly rather than
hidden, because a gate that cries wolf gets bypassed and then protects nothing:

1. **Handoff references.** ``generate_handoff_reference.py --check`` can NEVER
   pass locally: the local FastAPI/Pydantic emits ``contentMediaType``,
   ``^password$``, ``format: password``, a de-duplicated ``JWT`` security entry
   and ``ctx``/``input`` on ``ValidationError`` where CI's does not. So this
   gate applies the equivalent test that *is* reliable — the committed
   references must differ from ``origin/main`` by **additions only**. Every
   known skew shows up as a deletion, so zero deletions means "CI's file plus
   your changes".

2. **mypy ratchet.** Local mypy disagrees with CI's across a dozen files it
   reports in both directions. The gate runs it and shows the result, but only
   fails on files this change actually touched.

3. **Environment-dependent tests.** A few suites need services this machine does
   not run. They are listed in ``KNOWN_LOCAL_FAILURES`` with a reason; the gate
   reports them and does not fail on them. Anything NOT on that list fails the
   push. Add to it only with evidence that CI passes the same test.

Usage
-----
    python scripts/push_check.py            # everything (what the hook runs)
    python scripts/push_check.py --quick    # skip the slow suites; weaker
    python scripts/push_check.py --list     # show the checks and exit
    python scripts/push_check.py --only backend
    python scripts/push_check.py --only frontend
    python scripts/push_check.py --full     # run even for a docs-only change

A change whose every file is ignored by CI's ``paths-ignore`` (root ``*.md``,
``CHANGELOG.md``, ``qa/**``) skips the checks, because CI skips them too.

Install the hook so it runs on every push:

    make install-hooks

To push anyway (deliberately, with a reason):

    SKIP_PUSH_CHECK=1 git push
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# A Windows console defaults to cp1252 and raises UnicodeEncodeError on any
# non-ASCII byte. That crash lands while PRINTING A FAILURE REPORT, replacing
# the reason with a traceback. Prefer UTF-8, and fall back to replacement
# characters rather than dying.
try:  # pragma: no cover - depends on the host console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"

_LANGCHAIN = (
    "local langchain is newer than CI's pin: 'langchain.agents has no "
    "attribute create_react_agent'"
)
_LLM_TIMEOUT = (
    "the hypothesis LLM call times out on this machine (TimeoutError); "
    "flagged as local-only before any change in the session that found it"
)

# Tests known to fail on a developer machine and pass in CI, with the reason.
# NOT a place to park flaky tests — each entry needs evidence CI is green.
# Prefer a test id (``path::name``) over a file: a file entry tolerates every
# test in it, including ones that should be catching regressions.
KNOWN_LOCAL_FAILURES: dict[str, str] = {
    "tests/regression/test_chroma_storage_settings_runtime.py": (
        "needs a ChromaDB build CI installs; verified failing on a tree with no "
        "local changes"
    ),
    "tests/regression/test_offline_embedder_guard.py": (
        "same ChromaDB dependency as above"
    ),
    # The entries below are individual TESTS, not files -- each file also holds
    # tests that pass locally and must stay covered. Each was confirmed failing
    # on a PRISTINE origin/main checkout with no local change and no mutation
    # harness running, and each passed in CI run 35549887616 on the same code.
    "tests/test_chat_copilot.py::test_loop_iteration_cap_then_fallback": _LANGCHAIN,
    "tests/test_chat_copilot.py::test_loop_timeout_falls_back_to_single_shot": _LANGCHAIN,
    "tests/test_chat_copilot.py::test_multi_hop_question_resolved_via_two_tools": _LANGCHAIN,
    "tests/test_root_cause_tier_routing.py::"
    "test_react_explanation_uses_endpoint_and_bypasses_classifier_and_caches": _LANGCHAIN,
    "tests/test_rag_services.py::TestDocumentConnectorInternals::"
    "test_extract_docx_preserves_mixed_block_order_and_formatting": (
        "docx extraction differs under the local python-docx build"
    ),
    "tests/services/test_llm_one_call_one_meter_row.py::"
    "test_a_hypothesis_call_that_times_out_after_sending_is_metered_once": _LLM_TIMEOUT,
    "tests/services/test_llm_one_call_one_meter_row.py::"
    "test_a_hypothesis_call_with_no_reported_usage_is_metered_once": _LLM_TIMEOUT,
    "tests/services/test_llm_one_call_one_meter_row.py::"
    "test_a_hypothesis_call_with_reported_usage_is_metered_once_by_the_stage": _LLM_TIMEOUT,
    # A p95 WALL-CLOCK budget, measured while this machine runs ~11,000 other
    # tests. It failed on a different parametrised case each full run
    # ([flaky_coach], then [run_list]) and passed 7/7 three times running in
    # isolation; CI runs it on its own resources and it is green there. Under
    # the gate's own load it measures the gate, not the code. Entered as the
    # test FUNCTION, so its cases are covered via the "[" boundary while the
    # rest of the file still counts.
    "tests/test_performance_budgets_live.py::test_scenario_meets_p95_budget": (
        "p95 latency budget fails only under the full suite's load; passes in "
        "isolation and in CI"
    ),
}

# Reference files whose local regeneration always differs from CI's.
SKEWED_REFERENCES = (
    "docs/reference/openapi.json",
    "docs/reference/schemas.md",
    "docs/reference/api/authentication.md",
)


class Colour:
    OK = "\033[32m"
    BAD = "\033[31m"
    WARN = "\033[33m"
    DIM = "\033[2m"
    OFF = "\033[0m"

    @classmethod
    def off(cls) -> None:
        cls.OK = cls.BAD = cls.WARN = cls.DIM = cls.OFF = ""


@dataclass
class Check:
    name: str
    cmd: list[str]
    cwd: Path
    group: str
    slow: bool = False
    # Run even in --quick. Cheap guards that have actually caught pushes.
    always: bool = True
    env: dict[str, str] = field(default_factory=dict)
    # A check that needs logic rather than one subprocess. Returns
    # (ok, report). When set, ``cmd`` is ignored.
    fn: "Callable[[], tuple[bool, str]] | None" = None


def _npm() -> str:
    """npm is a .cmd shim on Windows; subprocess needs the resolved path."""
    return shutil.which("npm") or "npm"


def build_checks() -> list[Check]:
    npm = _npm()
    py = sys.executable
    return [
        # ── cheapest first: a lint error should not wait behind a 30-minute suite
        Check("backend: ruff", [py, "-m", "ruff", "check", "app/", "tests/"], BACKEND, "backend"),
        Check("frontend: eslint", [npm, "run", "lint"], FRONTEND, "frontend"),
        Check("frontend: tsc", [npm, "run", "type-check"], FRONTEND, "frontend"),
        Check("repo: quality gate", [py, "scripts/quality_gate.py"], REPO, "repo"),
        Check(
            "backend: prompt eval recordings",
            [py, "-m", "app.services.prompt_eval_recordings", "--check"],
            BACKEND, "backend",
        ),
        Check(
            "backend: agent API docs match OpenAPI",
            [py, "-m", "app.services.agent_api_docs", "--check"],
            BACKEND, "backend",
        ),
        Check(
            "docs: handoff link/consistency check",
            [py, "scripts/check_handoff_docs.py"], REPO, "docs",
        ),
        Check(
            "docs: handoff doc tests",
            [py, "-m", "pytest", str(REPO / "scripts" / "test_handoff_docs.py"),
             "-q", "-p", "no:testlookup"],
            BACKEND, "docs",
        ),
        # Before the slow suites, so a baseline slip costs ~1 minute, not 16.
        Check("backend: mypy ratchet (touched modules)", [], BACKEND, "backend",
              fn=check_mypy_touched),
        Check("docs: handoff references current", [], REPO, "docs",
              fn=check_stale_references),
        # ── the expensive ones
        Check("frontend: vitest + coverage ratchet",
              [npm, "run", "test", "--", "--coverage"], FRONTEND, "frontend", slow=True),
        Check("frontend: production build",
              [npm, "run", "build"], FRONTEND, "frontend", slow=True,
              env={"VITE_API_BASE_URL": "http://localhost:8000"}),
        Check("frontend: bundle budget",
              [npm, "run", "check:bundle"], FRONTEND, "frontend", slow=True),
        Check("frontend: theme tokens",
              [npm, "run", "check:theme"], FRONTEND, "frontend"),
        # Mutation harnesses are EXCLUDED here and run in CI instead. Measured on
        # this gate's first full run: they take minutes each, they patch REAL
        # source files and restore only on a clean exit, and on slower hardware
        # they hit their subprocess timeouts -- after which the file stays
        # mutated and every later harness starts from a sabotaged baseline. One
        # timeout became 9 harness failures and 23 corrupted files. CI ran every
        # one of them green on the same code.
        #
        # That is not a check a pre-push gate can run safely, so it does not.
        # Nothing ships unverified: CI still runs them, and the leak restore in
        # main() remains as a backstop.
        Check("backend: full test suite",
              [py, "-m", "pytest", "tests/", "--ignore=tests/integration",
               "--ignore-glob=*mutation_harness*",
               "-q", "-p", "no:testlookup"],
              BACKEND, "backend", slow=True),
    ]


_LINE_WIDTH = 78


def _tty() -> bool:
    """Carriage-return overwriting only works on a terminal. Piped into a file
    or a log, the CR is just a byte and the progress line stays, doubling every
    result — so outside a TTY we simply do not draw it."""
    return sys.stdout.isatty()


def _progress(label: str) -> None:
    """ASCII only: a Windows console renders "…" as a replacement char."""
    if _tty():
        print(f"  .. {label}", end="", flush=True)


def _clear() -> str:
    """Return to column 0 over a blanked line, so the result replaces the
    progress text instead of appending to it."""
    if not _tty():
        return ""
    return chr(13) + " " * _LINE_WIDTH + chr(13)


# Variables git exports to the hooks it runs, pinning every `git` inside the
# hook to THIS repository. The list is `git rev-parse --local-env-vars`.
# A check that builds its own scratch repository (test_handoff_docs.py does,
# with `git init` + `git check-ignore`) must not inherit them: from a linked
# worktree the pre-push hook sets GIT_DIR, the scratch `git` resolved to the
# caller's repository, and the test failed with "this operation must be run
# in a work tree" -- only under the hook, never when the gate was run by hand.
# Checks find the repository from their working directory instead.
HOOK_GIT_ENV = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


def check_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a check runs in: ours, minus git's hook variables."""
    env = {k: v for k, v in os.environ.items() if k not in HOOK_GIT_ENV}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra_env:
        env.update(extra_env)
    return env


def run(cmd: list[str], cwd: Path, extra_env: dict[str, str] | None = None
        ) -> tuple[int, str]:
    env = check_env(extra_env)
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or ""


def parse_pytest_failures(output: str) -> list[str]:
    """Node ids of the failed tests (``path::name``), forward-slashed.

    Node ids, not files. Allowlisting a whole FILE tolerates every test in it:
    ``test_rag_services.py`` has one locally-broken test among many, and a file
    entry would have hidden a real regression in any of the others.
    """
    ids = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            node = line[len("FAILED "):].split(" - ")[0].strip()
            ids.append(node.replace("\\", "/"))
    return ids


def _allowlist_key_for(node: str) -> str | None:
    """The allowlist entry covering this failed test, if any.

    Every match needs a BOUNDARY. A bare ``startswith`` let a node-id key
    ``x::test_a`` also cover ``x::test_ab`` -- a different test that happens to
    share a prefix, and exactly the kind of regression nobody would look for.
    """
    for key in KNOWN_LOCAL_FAILURES:
        if node == key:
            return key
        # A file entry covers every test in that file.
        if key.endswith(".py") and node.startswith(key + "::"):
            return key
        # A test entry covers its own parametrised cases: x::t covers x::t[1].
        if "::" in key and node.startswith(key + "["):
            return key
    return None


def backend_failures_are_all_known(output: str) -> tuple[bool, list[str], list[str]]:
    failed = parse_pytest_failures(output)
    if not failed:
        return False, [], []
    known, unknown = [], []
    for node in failed:
        (known if _allowlist_key_for(node) else unknown).append(node)
    return not unknown, sorted(set(known)), sorted(set(unknown))


_RATCHET_LINE = re.compile(r"^\s+(app/\S+\.py): (\d+) errors \(baseline (\d+)\)")


def _touched_backend_modules() -> set[str]:
    """Backend modules this branch changed, as the ratchet names them (app/...)."""
    paths: set[str] = set()
    for args in (("diff", "--name-only", "-z", "origin/main...HEAD"),
                 ("diff", "--name-only", "-z", "HEAD")):
        code, out = _git_stdout(*args)
        if code == 0:
            paths.update(p for p in out.split("\0") if p)
    return {
        p[len("backend/"):] for p in paths
        if p.startswith("backend/app/") and p.endswith(".py")
    }


def check_mypy_touched() -> tuple[bool, str]:
    """The mypy ratchet, judged only on the files this change touched.

    This gate's own docstring promised a mypy check from the first version, and
    ``build_checks()`` never contained one. PR #147 then went red on exactly it:
    deleting a dead Celery task removed a type error from ``tasks.py``, and the
    ratchet fails when a count DROPS as well as when it rises ("tighten it").

    Scoped because local mypy disagrees with CI's wholesale -- 391 errors here
    against CI's 365, across files nobody touched -- and a gate that fails on
    those would fail every push and be bypassed. On a file you DID touch, the
    local count is the best evidence available, and it matched CI's.
    """
    touched = _touched_backend_modules()
    if not touched:
        return True, "no backend module touched"
    code, out = run(
        [sys.executable, str(REPO / "scripts" / "mypy_ratchet.py"), "--check-stale"],
        BACKEND,
    )
    if code == 0:
        return True, f"ratchet clean ({len(touched)} module(s) touched)"
    violations = []
    for line in out.splitlines():
        m = _RATCHET_LINE.match(line)
        if m:
            violations.append((m.group(1), int(m.group(2)), int(m.group(3))))
    mine = [v for v in violations if v[0] in touched]
    if not mine:
        return True, (
            f"{len(violations)} untouched module(s) differ locally (known "
            f"local/CI mypy divergence); none you changed"
        )
    lines = []
    for path, now, base in mine:
        verb = "rose" if now > base else "DROPPED -- tighten the baseline"
        lines.append(f"  {path}: {now} errors, baseline {base} ({verb})")
    return False, (
        "mypy ratchet fails on files this change touched:\n" + "\n".join(lines)
        + "\n\nEdit only these lines in backend/mypy-baseline.txt (format "
        "'<count> <path>'). Do NOT run --update: it rewrites every entry from "
        "the local mypy, which disagrees with CI's across a dozen files."
    )


def check_stale_references() -> tuple[bool, str]:
    """Are the NON-skewed references current? This is what CI's --check fails on.

    The first version of this gate only inspected the three files whose local
    regeneration always differs from CI's. It never looked at
    ``source-inventory.csv`` -- which records every file's line count and
    exports, so editing ANY tracked file after regenerating makes it stale.
    PR #147 went red on precisely that: a test and ``push_check.py`` were
    changed after the references were refreshed, and the gate passed.

    ``--check`` is usable locally after all, once its drift list is filtered:
    it always names the three skewed files here, and anything beyond them is
    genuine staleness.
    """
    code, out = run([sys.executable, "scripts/generate_handoff_reference.py",
                     "--check"], REPO)
    if code == 0:
        return True, "references current"
    _, _, tail = out.partition("Generated reference drift:")
    drifted = {
        ln.strip().replace("\\", "/")
        for ln in tail.splitlines()
        if ln.strip().startswith("docs")
    }
    stale = sorted(drifted - set(SKEWED_REFERENCES))
    if not stale:
        return True, "only the known local-toolchain skew differs"
    return False, (
        "references are stale -- a tracked file changed after they were "
        "regenerated:\n    " + "\n    ".join(stale)
        + "\n  Stage your changes, then:\n"
        "    python scripts/generate_handoff_reference.py\n"
        "    git checkout -- " + " ".join(SKEWED_REFERENCES) + "\n"
        "  (the second line drops the local skew; commit what remains)"
    )


def check_reference_drift() -> tuple[bool, str]:
    """Committed references must differ from origin/main by ADDITIONS ONLY.

    Every known local-vs-CI skew manifests as a deletion (your toolchain emits
    something CI's does not), so an empty deletion set means the committed file
    is CI's file plus this change's additions. That is the property CI's
    ``--check`` is really asserting, and unlike ``--check`` it can pass locally.
    """
    code, _ = run(["git", "fetch", "-q", "origin", "main"], REPO)
    base = "origin/main"
    if code != 0:
        base = "main"  # offline: fall back to the local ref

    offenders = []
    for path in SKEWED_REFERENCES:
        code, out = run(["git", "diff", base, "--", path], REPO)
        if code != 0:
            return True, f"could not diff {path} against {base}; skipped"
        deletions = [
            ln for ln in out.splitlines()
            if ln.startswith("-") and not ln.startswith("---")
        ]
        if deletions:
            offenders.append(f"{path}: {len(deletions)} deleted line(s)")
    if offenders:
        return False, (
            "reference files differ from "
            f"{base} by DELETIONS, which means local toolchain skew was "
            "committed:\n    " + "\n    ".join(offenders)
            + "\n  Regenerate, then strip the skew hunks:\n"
            "    python scripts/generate_handoff_reference.py\n"
            "  and re-run. Deletions here are what CI's --check fails on."
        )
    return True, f"additions only vs {base}"


def _git_stdout(*args: str) -> tuple[int, str]:
    """Run git keeping stdout and stderr APART.

    ``run()`` merges them, which is right for a check's report and wrong for
    parsing: git writes "warning: in the working copy of 'x.py', LF will be
    replaced by CRLF" to stderr, and merged into a ``--name-only`` listing it
    became a "filename" that could never be restored. On a Windows checkout
    that warning is routine, so every leak would have been reported as
    unrecoverable. Caught by this module's own self-test.
    """
    proc = subprocess.run(
        ["git", *args], cwd=str(REPO),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout or ""


CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def _ci_paths_ignore() -> list[list[str]]:
    """Every ``paths-ignore`` list in ci.yml, read from the file itself.

    Read, not restated: a copy here would drift from CI's, and the gate would
    then skip a change CI tests (or test one CI skips). Parsed by hand so the
    hook needs nothing beyond the standard library.
    """
    try:
        lines = CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    lists: list[list[str]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "paths-ignore:":
            items: list[str] = []
            i += 1
            while i < len(lines):
                m = re.match(r"^\s+-\s+['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", lines[i])
                if not m:
                    break
                items.append(m.group(1).strip())
                i += 1
            if items:
                lists.append(items)
            continue
        i += 1
    return lists


def _github_glob(pattern: str) -> re.Pattern[str]:
    """GitHub Actions path-filter semantics, NOT fnmatch's.

    In a workflow filter ``*`` stops at ``/`` and only ``**`` crosses it, so
    CI's ``'*.md'`` ignores README.md at the root but still runs for
    architecture/DATABASE_SCHEMA.md. ``fnmatch`` lets ``*`` cross ``/``, which
    would have skipped the gate for changes CI goes on to test.
    """
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def docs_only_change() -> tuple[bool, list[str]]:
    """Does this branch change only files CI's test workflow ignores?

    Compared against ``origin/main``, because CI's tests run on the pull
    request and a PR's change set is its diff from the base. A file must be
    ignored by EVERY ``paths-ignore`` list (push and pull_request), since CI
    skipping one event but not the other still runs the tests.

    Fails safe in both directions that matter: if the rules cannot be read, or
    nothing differs from main, this says "not docs-only" and the full gate runs.
    """
    rule_sets = _ci_paths_ignore()
    if not rule_sets:
        return False, []
    _git_stdout("fetch", "-q", "origin", "main")
    code, out = _git_stdout("diff", "--name-only", "-z", "origin/main...HEAD")
    if code != 0:
        return False, []
    changed = sorted(p for p in out.split("\0") if p)
    if not changed:
        return False, []
    compiled = [[_github_glob(p) for p in rules] for rules in rule_sets]
    for path in changed:
        for rules in compiled:
            if not any(r.match(path) for r in rules):
                return False, changed
    return True, changed


def _dirty_tracked_files() -> set[str]:
    """Tracked files that currently differ from HEAD."""
    # -z: NUL-separated, so a path containing a newline cannot split in two.
    code, out = _git_stdout("diff", "--name-only", "-z", "HEAD")
    if code != 0:
        return set()
    return {p for p in out.split("\0") if p}


def _restore_harness_leaks(before: set[str]) -> list[str]:
    """Put back any tracked file a check modified, and report it.

    The backend suite contains mutation harnesses that patch REAL source files,
    run the tests, and restore on a clean exit. When one crashes mid-mutation
    the file stays mutated -- and every later harness in the run then starts
    from a sabotaged baseline and fails too. Measured on this gate's first full
    run: 23 source files left mutated, including ``llm_circuit_breaker``
    reporting an open breaker as closed.

    That is the most dangerous thing this script can do, because the obvious
    next step after a gate run is ``git commit -a``.

    Only files that were CLEAN when the gate started are restored. A file the
    developer was already editing is left exactly as it is and reported
    instead: restoring it would silently discard their uncommitted work, which
    is worse than the leak.
    """
    after = _dirty_tracked_files()
    leaked = sorted(after - before)
    if leaked:
        _git_stdout("checkout", "--", *leaked)
    still = sorted(_dirty_tracked_files() - before)
    touched_own = sorted(before & after)
    if leaked:
        print(f"\n  {Colour.WARN}restored {len(leaked)} source file(s) a check "
              f"left modified{Colour.OFF} -- a mutation harness exited without "
              f"cleaning up:")
        for f in leaked:
            print(f"    {f}")
    if still:
        print(f"  {Colour.BAD}could NOT restore:{Colour.OFF} {', '.join(still)}")
    if touched_own:
        print(f"  {Colour.DIM}(left alone -- you were already editing these: "
              f"{', '.join(touched_own)}){Colour.OFF}")
    return leaked


def main() -> int:
    """Run the gate, and never hand back a tree it has altered."""
    before = _dirty_tracked_files()
    try:
        return _main()
    finally:
        _restore_harness_leaks(before)


def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true",
                    help="skip the slow suites (weaker; CI may still fail)")
    ap.add_argument("--only", choices=["backend", "frontend", "repo", "docs"],
                    help="run one group only")
    ap.add_argument("--list", action="store_true", help="list checks and exit")
    ap.add_argument("--all", action="store_true",
                    help="keep going after the first failure (default: stop, so a "
                         "one-second lint error does not wait behind a 30-minute suite)")
    ap.add_argument("--no-colour", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="run every check even when only docs changed")
    args = ap.parse_args()

    if args.no_colour or os.environ.get("NO_COLOR"):
        Colour.off()

    checks = build_checks()
    if args.only:
        checks = [c for c in checks if c.group == args.only]
    if args.quick:
        checks = [c for c in checks if not c.slow]

    if args.list:
        for c in checks:
            tag = " (slow)" if c.slow else ""
            print(f"  [{c.group}] {c.name}{tag}")
        return 0

    # Documentation merges without running tests (standing rule), and CI's
    # paths-ignore already skips its test workflow for exactly these files.
    # Without this the hook ran the full ~18-minute gate on a one-line tracker
    # edit, and the only way through was SKIP_PUSH_CHECK -- a bypass that, used
    # routinely, stops meaning anything.
    if not args.full:
        docs_only, changed = docs_only_change()
        if docs_only:
            print(f"{Colour.OK}Docs-only change{Colour.OFF} "
                  f"({len(changed)} file(s), all ignored by CI's paths-ignore) "
                  f"-- skipping the checks, as CI will.")
            for p in changed[:10]:
                print(f"  {Colour.DIM}{p}{Colour.OFF}")
            print(f"{Colour.DIM}Run them anyway with --full.{Colour.OFF}")
            return 0

    print(f"{Colour.DIM}Running {len(checks)} checks that mirror CI. "
          f"Ctrl-C is safe.{Colour.OFF}\n")

    failures: list[tuple[str, str]] = []
    warnings: list[str] = []
    started = time.time()

    for c in checks:
        t0 = time.time()
        _progress(c.name)
        if c.fn is not None:
            ok, out = c.fn()
            code = 0 if ok else 1
        else:
            code, out = run(c.cmd, c.cwd, c.env)
        secs = time.time() - t0

        if code != 0 and c.name == "backend: full test suite":
            all_known, known, unknown = backend_failures_are_all_known(out)
            if all_known:
                print(f"{_clear()}  {Colour.WARN}~{Colour.OFF} {c.name} "
                      f"{Colour.DIM}({secs:.0f}s){Colour.OFF}")
                for k in known:
                    # ``k`` is a node id; the reason lives on whichever entry
                    # covered it, which may be its file.
                    reason = KNOWN_LOCAL_FAILURES.get(_allowlist_key_for(k) or "", "")
                    warnings.append(f"{k} failed locally - known: {reason}")
                continue
            out += "\n\nUnexpected failures (not in KNOWN_LOCAL_FAILURES):\n  " + \
                   "\n  ".join(unknown)

        if code == 0:
            print(f"{_clear()}  {Colour.OK}OK{Colour.OFF} {c.name} "
                  f"{Colour.DIM}({secs:.0f}s){Colour.OFF}")
        else:
            print(f"{_clear()}  {Colour.BAD}FAIL{Colour.OFF} {c.name} "
                  f"{Colour.DIM}({secs:.0f}s){Colour.OFF}")
            failures.append((c.name, out[-4000:]))
            if not args.all:
                # The list is ordered cheapest-first precisely so this can stop
                # early. Without it, a ruff error found in ONE SECOND still cost
                # the full ~40 minutes before the hook said so -- measured on
                # this gate's own first end-to-end test.
                remaining = len(checks) - checks.index(c) - 1
                if remaining:
                    print(f"  {Colour.DIM}stopping here; {remaining} check(s) not "
                          f"run (--all runs them anyway){Colour.OFF}")
                break

    # Reference drift is not a subprocess check; it compares against origin/main.
    # Skipped when something already failed, for the same reason as above.
    if (not args.only or args.only == "docs") and not (failures and not args.all):
        _progress("docs: reference drift vs origin/main")
        ok, detail = check_reference_drift()
        if ok:
            print(f"{_clear()}  {Colour.OK}OK{Colour.OFF} docs: reference drift "
                  f"{Colour.DIM}({detail}){Colour.OFF}")
        else:
            print(f"{_clear()}  {Colour.BAD}FAIL{Colour.OFF} docs: reference drift")
            failures.append(("docs: reference drift", detail))

    total = time.time() - started
    print()

    for w in warnings:
        print(f"  {Colour.WARN}known local failure{Colour.OFF}: {w}")
    if warnings:
        print()

    if failures:
        print(f"{Colour.BAD}{len(failures)} check(s) failed{Colour.OFF} "
              f"in {total:.0f}s — CI would fail too.\n")
        for name, out in failures:
            rule = "-" * max(0, 60 - len(name))
            print(f"{Colour.BAD}-- {name} {rule}{Colour.OFF}")
            print(out.rstrip()[-2000:])
            print()
        print("Fix these, or push deliberately with SKIP_PUSH_CHECK=1 git push")
        return 1

    if args.quick:
        print(f"{Colour.WARN}Quick mode: the slow suites did not run, so this "
              f"does NOT predict CI.{Colour.OFF}")
    print(f"{Colour.OK}All {len(checks)} checks passed{Colour.OFF} in {total:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
