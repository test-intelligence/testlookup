"""Self-test for the pre-push gate.

A gate is only worth the pushes it blocks, and the dangerous failure mode is the
opposite of the obvious one: not "it fails too often" but "it passes when CI
would not". Two mechanisms here can do that, so both are pinned:

* ``KNOWN_LOCAL_FAILURES`` — an allowlist of tests that fail on a developer
  machine and pass in CI. If it ever matched loosely it would swallow a real
  regression sitting in the same run.
* the reference-drift check — it must treat DELETIONS against ``origin/main``
  as the failure, because every known local-toolchain skew appears as one.

Written after the gate crashed on its own failure report (Windows cp1252 could
not encode the box-drawing characters), which is exactly the moment the report
matters most.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "push_check.py"
spec = importlib.util.spec_from_file_location("push_check", SCRIPT)
assert spec and spec.loader
push_check = importlib.util.module_from_spec(spec)
# Registered BEFORE exec: ``@dataclass`` resolves annotations through
# ``sys.modules[cls.__module__]``, which is None for a module that is still
# executing, and the decorator then dies with an opaque AttributeError.
sys.modules["push_check"] = push_check
spec.loader.exec_module(push_check)


class TestChecksDoNotInheritTheHooksRepository:
    """Git exports GIT_DIR (and friends) to the hooks it runs. The pre-push hook
    from a linked worktree failed test_handoff_docs.py, which builds its own
    scratch repository, while the same gate run by hand passed."""

    def test_the_strip_list_covers_everything_git_calls_repository_local(self):
        code, out = push_check._git_stdout("rev-parse", "--local-env-vars")
        assert code == 0, out
        listed = {line.strip() for line in out.splitlines() if line.strip()}
        assert listed, "git listed no variables; the comparison would be vacuous"
        assert listed <= set(push_check.HOOK_GIT_ENV)

    def test_the_check_env_drops_them_and_keeps_everything_else(self, monkeypatch):
        monkeypatch.setenv("GIT_DIR", "/somewhere/.git")
        monkeypatch.setenv("GIT_WORK_TREE", "/somewhere")
        monkeypatch.setenv("KEEP_ME", "1")
        env = push_check.check_env({"EXTRA": "x"})
        assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
        assert env["KEEP_ME"] == "1" and env["EXTRA"] == "x"

    def test_a_scratch_repository_is_itself_under_a_hooks_environment(
        self, monkeypatch, tmp_path
    ):
        code, own = push_check._git_stdout("rev-parse", "--absolute-git-dir")
        assert code == 0, own
        # What the hook hands every child process.
        monkeypatch.setenv("GIT_DIR", own.strip())
        scratch = tmp_path / "scratch"
        code, out = push_check.run(["git", "init", "-q", str(scratch)], tmp_path)
        assert code == 0, out
        code, out = push_check.run(["git", "rev-parse", "--absolute-git-dir"], scratch)
        assert code == 0, out
        assert Path(out.strip()).resolve() == (scratch / ".git").resolve()


class TestReferenceDriftAcceptsARealApiChange:
    """The drift check once treated EVERY deletion in the skewed reference
    files as toolchain skew, so a change that rewrote an existing parameter
    (VIZ-201: ``release_id`` became repeatable) could never pass the pre-push
    hook. Deletions are now accepted exactly when the committed file is CI's
    ``origin/main`` file plus the change's own delta, derived from two LOCAL
    generations whose skew cancels."""

    CI = "a\nb-ci-form\nc\nd\n"            # what CI committed on main
    LOCAL_BASE = "a\nb-local-skew\nc\nd\n"  # main as THIS toolchain renders it
    LOCAL_HEAD = "a\nb-local-skew\nc\nD\n"  # the branch, same toolchain

    def test_the_merge_is_ci_plus_the_real_delta(self):
        # The skewed line keeps CI's form; the real change (d -> D) lands.
        assert push_check.merged_reference(self.CI, self.LOCAL_BASE, self.LOCAL_HEAD) == (
            "a\nb-ci-form\nc\nD\n"
        )

    def test_a_delta_that_touches_a_skewed_line_has_no_exact_answer(self):
        head = "a\nb-changed\nc\nd\n"
        assert push_check.merged_reference(self.CI, self.LOCAL_BASE, head) is None

    def test_crlf_does_not_count_as_a_difference(self):
        crlf = self.CI.replace("\n", "\r\n")
        assert push_check.merged_reference(crlf, self.LOCAL_BASE, self.LOCAL_HEAD) == (
            "a\nb-ci-form\nc\nD\n"
        )

    def _fake_refs(self, monkeypatch, tmp_path, current):
        refs = {"origin/main": self.LOCAL_BASE, "HEAD": self.LOCAL_HEAD}
        seen = []

        def fake_generate(ref, workdir, *, with_working_tree=False):
            seen.append((ref, with_working_tree))
            return {p: refs[ref] for p in push_check.SKEWED_REFERENCES}

        monkeypatch.setattr(push_check, "_generate_references_at", fake_generate)
        monkeypatch.setattr(push_check, "_git_stdout", lambda *args: (0, self.CI))
        # "This checkout" is read from disk, as the deletion scan reads it.
        monkeypatch.setattr(push_check, "REPO", tmp_path)
        for p in push_check.SKEWED_REFERENCES:
            (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / p).write_bytes(current.encode("utf-8"))
        return seen

    def test_committed_equal_to_ci_plus_delta_is_real(self, monkeypatch, tmp_path):
        seen = self._fake_refs(monkeypatch, tmp_path, "a\nb-ci-form\nc\nD\n")
        real, why = push_check.reference_delta_is_real("origin/main")
        assert real, why
        # The head side must describe the checkout, not just the last commit.
        assert ("HEAD", True) in seen and ("origin/main", False) in seen

    def test_committed_local_skew_is_not_real(self, monkeypatch, tmp_path):
        # Committing the raw local generation carries the skewed line.
        self._fake_refs(monkeypatch, tmp_path, self.LOCAL_HEAD)
        real, why = push_check.reference_delta_is_real("origin/main")
        assert not real
        assert "not CI's file plus this change's delta" in why

    def test_a_generation_failure_fails_closed(self, monkeypatch):
        def boom(ref, workdir, **_):
            raise RuntimeError("worktree add failed")

        monkeypatch.setattr(push_check, "_generate_references_at", boom)
        real, why = push_check.reference_delta_is_real("origin/main")
        assert not real and "worktree add failed" in why

    def _drift_with(self, monkeypatch, diff_text, real):
        def fake_run(cmd, cwd, extra_env=None):
            if cmd[:2] == ["git", "fetch"]:
                return 0, ""
            return 0, diff_text

        monkeypatch.setattr(push_check, "run", fake_run)
        calls = []

        def fake_real(base):
            calls.append(base)
            return real, "stub"

        monkeypatch.setattr(push_check, "reference_delta_is_real", fake_real)
        return push_check.check_reference_drift(), calls

    def test_additions_only_never_derive_the_delta(self, monkeypatch):
        (ok, _), calls = self._drift_with(monkeypatch, "+++ b/x\n+added\n", real=False)
        assert ok and calls == []

    def test_deletions_that_are_the_real_delta_pass(self, monkeypatch):
        (ok, detail), calls = self._drift_with(monkeypatch, "--- a/x\n-removed\n", real=True)
        assert ok and calls == ["origin/main"] and "real API change" in detail

    def test_deletions_that_are_not_the_real_delta_fail(self, monkeypatch):
        (ok, detail), _ = self._drift_with(monkeypatch, "--- a/x\n-removed\n", real=False)
        assert not ok and "not this change's own API delta" in detail


class TestParsingPytestOutput:
    def test_it_extracts_the_node_id_not_just_the_file(self):
        # Node ids, because a FILE-level allowlist entry tolerates every test in
        # that file -- including the ones meant to catch regressions.
        out = (
            "FAILED tests/test_a.py::test_one - AssertionError\n"
            "FAILED tests/sub/test_b.py::TestX::test_two - ValueError\n"
        )
        assert push_check.parse_pytest_failures(out) == [
            "tests/test_a.py::test_one",
            "tests/sub/test_b.py::TestX::test_two",
        ]

    def test_a_clean_run_yields_nothing(self):
        assert push_check.parse_pytest_failures("1234 passed in 60s") == []

    def test_a_test_named_failed_is_not_a_failure(self):
        # The suite is full of tests whose NAMES contain "failed"
        # (test_a_failed_backup_job_alerts). Matching anywhere in the line would
        # read a passing run as a failing one.
        out = "tests/test_x.py::test_a_failed_backup_job_alerts PASSED [ 8%]"
        assert push_check.parse_pytest_failures(out) == []

    def test_windows_paths_are_normalised(self):
        # pytest reports backslashes on Windows; the allowlist is written with
        # forward slashes, and a mismatch would silently fail to match.
        out = "FAILED tests\\regression\\test_x.py::test_one - Error"
        assert push_check.parse_pytest_failures(out) == [
            "tests/regression/test_x.py::test_one"
        ]


class TestTheAllowlistCannotHideARegression:
    """The failure mode that matters: passing when CI would fail."""

    def test_a_known_failure_alone_is_tolerated(self):
        known = next(iter(push_check.KNOWN_LOCAL_FAILURES))
        out = f"FAILED {known}::test_something - ImportError\n"
        all_known, matched, unknown = push_check.backend_failures_are_all_known(out)
        assert all_known is True
        assert matched == [f"{known}::test_something"]
        assert unknown == []

    def test_one_unknown_failure_beside_a_known_one_still_fails(self):
        # The whole point. A real regression must not ride along with an
        # environmental failure in the same run.
        known = next(iter(push_check.KNOWN_LOCAL_FAILURES))
        out = (
            f"FAILED {known}::test_something - ImportError\n"
            "FAILED tests/test_real_regression.py::test_it - AssertionError\n"
        )
        all_known, _, unknown = push_check.backend_failures_are_all_known(out)
        assert all_known is False
        assert unknown == ["tests/test_real_regression.py::test_it"]

    def test_a_clean_run_is_not_reported_as_all_known(self):
        # No failures must not be mistaken for "all failures are known", which
        # would make the caller skip the success path.
        all_known, _, _ = push_check.backend_failures_are_all_known("100 passed")
        assert all_known is False

    def test_the_allowlist_is_not_a_prefix_wildcard_for_a_directory(self):
        # Entries are a test file or a test id. If someone shortened one to
        # "tests/regression", every regression failure would be tolerated.
        for entry in push_check.KNOWN_LOCAL_FAILURES:
            assert entry.endswith(".py") or ".py::" in entry, (
                f"{entry!r} is neither a test file nor a test id; a directory "
                "entry would swallow every failure beneath it"
            )

    def test_a_test_id_entry_does_not_cover_a_test_sharing_its_prefix(self, monkeypatch):
        # The latent bug in the first matcher: a bare startswith() let
        # "x::test_a" also tolerate "x::test_ab" -- a different test entirely.
        monkeypatch.setattr(push_check, "KNOWN_LOCAL_FAILURES",
                            {"tests/test_x.py::test_a": "env"})
        out = "FAILED tests/test_x.py::test_ab - AssertionError"
        all_known, _, unknown = push_check.backend_failures_are_all_known(out)
        assert all_known is False
        assert unknown == ["tests/test_x.py::test_ab"]

    def test_a_test_id_entry_covers_its_own_parametrised_cases(self, monkeypatch):
        monkeypatch.setattr(push_check, "KNOWN_LOCAL_FAILURES",
                            {"tests/test_x.py::test_a": "env"})
        out = "FAILED tests/test_x.py::test_a[case-1] - AssertionError"
        assert push_check.backend_failures_are_all_known(out)[0] is True

    def test_a_file_entry_covers_its_tests_and_no_other_file(self, monkeypatch):
        monkeypatch.setattr(push_check, "KNOWN_LOCAL_FAILURES",
                            {"tests/test_x.py": "env"})
        assert push_check.backend_failures_are_all_known(
            "FAILED tests/test_x.py::anything - E")[0] is True
        # "test_x.py" must not also cover "test_xy.py".
        assert push_check.backend_failures_are_all_known(
            "FAILED tests/test_xy.py::anything - E")[0] is False

    def test_the_langchain_entries_are_single_tests_not_whole_files(self):
        # test_rag_services.py holds one locally-broken test among many; a file
        # entry would have hidden a regression in any of the others.
        for entry in push_check.KNOWN_LOCAL_FAILURES:
            if any(n in entry for n in ("rag_services", "chat_copilot", "root_cause")):
                assert "::" in entry, f"{entry} is allowlisted as a whole file"

    def test_every_allowlist_entry_carries_a_reason(self):
        for entry, reason in push_check.KNOWN_LOCAL_FAILURES.items():
            assert reason.strip(), f"{entry} is allowlisted with no reason"


class TestTheGateNeverHandsBackAnAlteredTree:
    """The most dangerous thing this script can do.

    The backend suite contains mutation harnesses that patch real source files
    and restore them only on a clean exit. The gate's first full run left 23
    source files mutated -- ``llm_circuit_breaker`` reporting an open breaker as
    closed among them -- and the natural next step after a gate is a commit.
    Tested in a throwaway repository so the assertion does not depend on a
    harness happening to crash.
    """

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        import subprocess

        def git(*a):
            subprocess.run(["git", *a], cwd=tmp_path, check=True,
                           capture_output=True)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        # Pin line endings. With the host's core.autocrlf=true, checkout
        # rewrites LF as CRLF and a byte comparison fails even though the
        # restore worked -- the first run of this test failed on exactly that.
        git("config", "core.autocrlf", "false")
        (tmp_path / "service.py").write_bytes(b"OPEN = 1.0\n")
        (tmp_path / "mine.py").write_bytes(b"x = 1\n")
        git("add", ".")
        git("commit", "-q", "-m", "base")
        monkeypatch.setattr(push_check, "REPO", tmp_path)
        return tmp_path

    def test_a_file_a_harness_mutated_is_put_back(self, repo):
        before = push_check._dirty_tracked_files()
        assert before == set()

        # What a crashed harness leaves behind.
        (repo / "service.py").write_bytes(b"OPEN = 0.0\n")

        leaked = push_check._restore_harness_leaks(before)
        assert leaked == ["service.py"]
        assert (repo / "service.py").read_bytes() == b"OPEN = 1.0\n", (
            "the mutation survived the gate"
        )

    def test_the_developers_own_edit_is_never_discarded(self, repo):
        # They were already editing this when the gate started. Restoring it
        # would silently throw their work away -- worse than any leak.
        (repo / "mine.py").write_bytes(b"x = 2  # in progress\n")
        before = push_check._dirty_tracked_files()
        assert before == {"mine.py"}

        push_check._restore_harness_leaks(before)
        assert (repo / "mine.py").read_bytes() == b"x = 2  # in progress\n"

    def test_a_clean_run_changes_nothing(self, repo):
        before = push_check._dirty_tracked_files()
        assert push_check._restore_harness_leaks(before) == []

    def test_main_restores_even_when_a_check_raises(self, repo, monkeypatch):
        # The restore lives in a `finally`. Fail-fast, Ctrl-C and a crash all
        # leave the loop early -- exactly the cases where a harness is likeliest
        # to have left something behind.
        def boom():
            (repo / "service.py").write_bytes(b"OPEN = 0.0\n")
            raise RuntimeError("check crashed")

        monkeypatch.setattr(push_check, "_main", boom)
        with pytest.raises(RuntimeError):
            push_check.main()
        assert (repo / "service.py").read_bytes() == b"OPEN = 1.0\n"


class TestTheMypyRatchetIsActuallyChecked:
    """The gate's docstring promised this from the first version and
    ``build_checks()`` never contained it. PR #147 then failed CI on exactly it:
    deleting a dead task dropped ``tasks.py`` from 31 errors to 30, and the
    ratchet fails on a DROP as well as a rise."""

    RATCHET_OUT = (
        "mypy: 365 errors in 114 files (baseline 366)\n"
        "::error::fewer errors than the baseline allows; tighten it\n"
        "  app/worker/tasks.py: 30 errors (baseline 31)\n"
        "  app/routers/integration_health.py: 2 errors (baseline 1)\n"
    )

    def _stub(self, monkeypatch, touched):
        monkeypatch.setattr(push_check, "_touched_backend_modules", lambda: touched)
        monkeypatch.setattr(push_check, "run", lambda *a, **k: (1, self.RATCHET_OUT))

    def test_the_check_is_registered(self):
        # The exact gap: a promise in the docstring, nothing in the list.
        names = [c.name for c in push_check.build_checks()]
        assert any("mypy" in n for n in names), "no mypy check in build_checks()"

    def test_it_runs_before_the_slow_suites(self):
        # A baseline slip should cost a minute, not wait behind 15.
        names = [c.name for c in push_check.build_checks()]
        mypy_at = next(i for i, n in enumerate(names) if "mypy" in n)
        assert mypy_at < names.index("backend: full test suite")

    def test_a_drop_in_a_touched_module_fails_the_push(self, monkeypatch):
        self._stub(monkeypatch, {"app/worker/tasks.py"})
        ok, msg = push_check.check_mypy_touched()
        assert ok is False
        assert "app/worker/tasks.py" in msg
        assert "DROPPED" in msg, "a drop must be named as a drop, not a rise"

    def test_divergence_in_untouched_modules_does_not_fail(self, monkeypatch):
        # Local mypy disagrees with CI's across files nobody touched. Failing on
        # those would fail every push, and a gate that always fails is bypassed.
        self._stub(monkeypatch, {"app/services/something_else.py"})
        ok, _ = push_check.check_mypy_touched()
        assert ok is True

    def test_it_never_advises_update(self, monkeypatch):
        # --update rewrites all ~116 entries from the local mypy, which is the
        # wrong mypy. The remediation has to say edit the one line.
        self._stub(monkeypatch, {"app/worker/tasks.py"})
        _, msg = push_check.check_mypy_touched()
        assert "Do NOT run --update" in msg

    def _skewed(self, monkeypatch, local, baseline, skew):
        monkeypatch.setattr(push_check, "_touched_backend_modules",
                            lambda: {"app/skewed.py"})
        out = f"  app/skewed.py: {local} errors (baseline {baseline})\n"
        monkeypatch.setattr(push_check, "run", lambda *a, **k: (1, out))
        monkeypatch.setattr(push_check, "KNOWN_MYPY_SKEW",
                            {"app/skewed.py": (skew, "CI log shows +1")})

    def test_a_documented_skew_that_explains_the_gap_passes(self, monkeypatch):
        # PR #153: CI counts bootstrap.py at 2, local mypy at 1. The baseline
        # holds CI's 2; the local 1 is not a drop to "tighten".
        self._skewed(monkeypatch, local=1, baseline=2, skew=1)
        ok, msg = push_check.check_mypy_touched()
        assert ok is True, msg

    def test_a_skew_does_not_excuse_a_further_change(self, monkeypatch):
        # One more real error than the skew accounts for still fails ...
        self._skewed(monkeypatch, local=2, baseline=2, skew=1)
        ok, msg = push_check.check_mypy_touched()
        assert ok is False and "rose" in msg
        # ... and so does a real fix beyond it.
        self._skewed(monkeypatch, local=0, baseline=2, skew=1)
        ok, msg = push_check.check_mypy_touched()
        assert ok is False and "DROPPED" in msg

    def test_every_documented_skew_names_its_evidence(self):
        for path, (delta, why) in push_check.KNOWN_MYPY_SKEW.items():
            assert path.startswith("app/") and delta != 0
            assert "CI" in why and len(why) > 40, f"{path}: say where CI showed it"

    def test_no_backend_change_skips_the_slow_mypy_run(self, monkeypatch):
        called = []
        monkeypatch.setattr(push_check, "_touched_backend_modules", lambda: set())
        monkeypatch.setattr(push_check, "run", lambda *a, **k: called.append(1) or (0, ""))
        ok, _ = push_check.check_mypy_touched()
        assert ok is True and called == []


class TestStaleReferencesAreCaught:
    """PR #147 failed CI on a stale ``source-inventory.csv`` that this gate
    passed: its reference check only looked at the three skewed files."""

    def _stub(self, monkeypatch, out, code=1):
        monkeypatch.setattr(push_check, "run", lambda *a, **k: (code, out))

    def test_it_is_registered_before_the_slow_suites(self):
        names = [c.name for c in push_check.build_checks()]
        at = names.index("docs: handoff references current")
        assert at < names.index("backend: full test suite")

    def test_only_the_known_skew_is_not_a_failure(self, monkeypatch):
        # What --check ALWAYS reports on this machine. Failing here would fail
        # every push.
        self._stub(monkeypatch, (
            "Generated reference drift:\n"
            "docs\\reference\\openapi.json\n"
            "docs\\reference\\api\\authentication.md\n"
            "docs\\reference\\schemas.md\n"))
        assert push_check.check_stale_references()[0] is True

    def test_a_stale_inventory_fails(self, monkeypatch):
        # The exact CI failure.
        self._stub(monkeypatch, (
            "Generated reference drift:\n"
            "docs\\reference\\openapi.json\n"
            "docs\\reference\\source-inventory.csv\n"))
        ok, msg = push_check.check_stale_references()
        assert ok is False
        assert "docs/reference/source-inventory.csv" in msg
        assert "openapi.json" not in msg.split("Stage")[0], (
            "the known skew must not be reported as staleness"
        )

    def test_windows_separators_are_normalised(self, monkeypatch):
        # --check prints backslashes here; the skew set is written with forward
        # slashes. Without normalising, the three skew files would never match
        # and every push would fail as "stale".
        self._stub(monkeypatch, (
            "Generated reference drift:\n"
            "docs\\reference\\schemas.md\n"))
        assert push_check.check_stale_references()[0] is True

    def test_a_clean_check_passes(self, monkeypatch):
        self._stub(monkeypatch, "References are current.\n", code=0)
        assert push_check.check_stale_references()[0] is True


class TestDocsOnlyPushesSkipTheGate:
    """Docs merge without tests (standing rule), and CI's paths-ignore already
    skips its test workflow for them. The gate must skip EXACTLY when CI does:
    skipping more lets an untested change through; skipping less makes the
    bypass variable routine, and a routine bypass protects nothing."""

    def _diff(self, monkeypatch, files):
        def fake_git(*args):
            if args[:1] == ("fetch",):
                return 0, ""
            return 0, "\0".join(files) + ("\0" if files else "")
        monkeypatch.setattr(push_check, "_git_stdout", fake_git)

    def test_the_rules_are_read_from_ci_yml(self):
        # Read, not restated -- a copy here would drift from CI's.
        rules = push_check._ci_paths_ignore()
        assert rules, "no paths-ignore found in ci.yml"
        for r in rules:
            assert "*.md" in r and "qa/**" in r

    @pytest.mark.parametrize("path,ignored", [
        ("README.md", True),
        ("LIVE_APP_BUG_TRACKER.md", True),
        ("CHANGELOG.md", True),
        ("qa/2026-09-19-01/defects/TL-1.md", True),
        # GitHub's '*' stops at '/': CI RUNS for these.
        ("architecture/DATABASE_SCHEMA.md", False),
        ("frontend/README.md", False),
        ("backend/app/x.py", False),
    ])
    def test_github_glob_semantics(self, path, ignored):
        # fnmatch lets '*' cross '/', which would skip the gate for nested
        # markdown CI still tests.
        rules = push_check._ci_paths_ignore()[0]
        hit = any(push_check._github_glob(p).match(path) for p in rules)
        assert hit is ignored, path

    def test_a_tracker_edit_is_docs_only(self, monkeypatch):
        self._diff(monkeypatch, ["LIVE_APP_BUG_TRACKER.md", "qa/x/y.md"])
        assert push_check.docs_only_change()[0] is True

    def test_one_code_file_makes_it_not_docs_only(self, monkeypatch):
        self._diff(monkeypatch, ["LIVE_APP_BUG_TRACKER.md", "scripts/push_check.py"])
        assert push_check.docs_only_change()[0] is False

    def test_nested_markdown_is_not_docs_only(self, monkeypatch):
        self._diff(monkeypatch, ["architecture/DATABASE_SCHEMA.md"])
        assert push_check.docs_only_change()[0] is False

    def test_an_empty_diff_runs_the_gate(self, monkeypatch):
        # Nothing to judge is not "only docs"; fail toward running.
        self._diff(monkeypatch, [])
        assert push_check.docs_only_change()[0] is False

    def test_unreadable_rules_run_the_gate(self, monkeypatch):
        # If ci.yml moves or cannot be parsed, skip nothing.
        monkeypatch.setattr(push_check, "_ci_paths_ignore", lambda: [])
        self._diff(monkeypatch, ["README.md"])
        assert push_check.docs_only_change()[0] is False

    def test_the_skip_runs_no_check(self, monkeypatch):
        monkeypatch.setattr(push_check, "docs_only_change",
                            lambda: (True, ["README.md"]))
        monkeypatch.setattr(push_check, "run", lambda *a, **k: pytest.fail(
            "a check ran on a docs-only push"))
        monkeypatch.setattr("sys.argv", ["push_check.py", "--no-colour"])
        assert push_check._main() == 0

    def test_full_runs_the_checks_anyway(self, monkeypatch):
        ran = []
        monkeypatch.setattr(push_check, "docs_only_change",
                            lambda: (True, ["README.md"]))
        monkeypatch.setattr(push_check, "build_checks", lambda: [
            push_check.Check("probe", [], push_check.REPO, "repo",
                             fn=lambda: (ran.append(1) or True, "ok"))])
        monkeypatch.setattr(push_check, "check_reference_drift",
                            lambda: (True, "ok"))
        monkeypatch.setattr("sys.argv", ["push_check.py", "--no-colour", "--full"])
        assert push_check._main() == 0
        assert ran == [1]


class TestOutputSurvivesAWindowsConsole:
    def test_nothing_printed_is_outside_ascii(self):
        """The gate crashed here once.

        A cp1252 console raises ``UnicodeEncodeError`` on the first non-ASCII
        byte, and the crash landed while printing the FAILURE REPORT — so the
        one run that needed an explanation produced a traceback instead.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]  # skip the module docstring
        offenders = []
        for lineno, line in enumerate(body.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("print(") and ".format" not in stripped:
                continue
            for ch in line:
                if ord(ch) > 127:
                    offenders.append((lineno, ch, line.strip()[:70]))
                    break
        assert not offenders, f"non-ASCII in printed output: {offenders}"


class TestTheCheckListMatchesCI:
    """Each entry exists because CI runs it and nothing local did."""

    @pytest.mark.parametrize(
        "needle",
        [
            '"run", "build"',        # the production build
            "check:bundle",
            "check:theme",
            "--coverage",            # the frontend coverage ratchet
            "prompt_eval_recordings",
            "agent_api_docs",
        ],
    )
    def test_the_step_that_once_escaped_is_present(self, needle):
        source = SCRIPT.read_text(encoding="utf-8")
        normalised = source.replace("'", '"')
        assert needle.replace("'", '"') in normalised, (
            f"{needle} is in CI but no longer in the gate"
        )

    def test_the_backend_suite_is_not_narrowed_to_regression(self):
        # Running only tests/regression is what let a broken
        # tests/test_summary_report_router.py through.
        source = SCRIPT.read_text(encoding="utf-8")
        assert '"tests/"' in source
        assert '"tests/regression"' not in source

    def test_it_stops_at_the_first_failure_by_default(self):
        """Ordering cheapest-first is worthless without the early exit.

        The first end-to-end test of the hook planted a ruff error — found in
        one second — and the gate still ran every remaining check before
        reporting it. A pre-push hook that takes 40 minutes to report a typo
        gets bypassed, and a bypassed gate protects nothing. Measured after the
        fix: 1s.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        assert "if not args.all:" in source, "the early exit is gone"
        assert "break" in source.split("if not args.all:")[1][:600], (
            "args.all is checked but the loop does not actually stop"
        )

    def test_all_is_opt_in_not_the_default(self):
        # If --all were the default the early exit would never fire.
        source = SCRIPT.read_text(encoding="utf-8")
        assert '"--all", action="store_true"' in source

    def test_the_slow_checks_are_marked_slow(self):
        # --quick must skip the expensive ones and nothing else, or it silently
        # becomes the full run and nobody uses it.
        checks = {c.name: c for c in push_check.build_checks()}
        assert checks["backend: full test suite"].slow
        assert checks["frontend: production build"].slow
        assert not checks["backend: ruff"].slow
