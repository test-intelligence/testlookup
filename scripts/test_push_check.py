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
