"""Tests for ``scripts/quality_gate.py``.

These pin the ratchet contract: a guard with a baseline tolerates the
listed entries and ONLY fails on new violations. They run without any
backend / frontend deps — pure file IO + the script's own helpers.

Run from the repo root::

    python -m pytest scripts/test_quality_gate.py -v

The tests stand on their own (no need for the backend test rig).
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

import quality_gate as qg


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")


def _redirect_repo_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the script at a throwaway tmp repo so we can craft violations
    deterministically without touching the real tree."""
    monkeypatch.setattr(qg, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(qg, "BASELINE_DIR", tmp_path / "scripts" / "quality-gate-baselines")
    # The guards reference REPO_ROOT inside their bodies via the module-
    # level constants list (_INGESTION_OWNERS, _ANALYSIS_ROUTER_ALLOWLIST,
    # etc.). Those are paths relative to REPO_ROOT so no further patching
    # is needed.


# ── grep_lines / python_string_lines ─────────────────────────────────────────


def test_python_string_lines_skips_docstring_matches(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    _write(f, '''
        """This is a module docstring that mentions run_triage_agent() but
        is just prose."""
        x = 1
        # This comment mentions run_triage_agent() too.
        result = run_triage_agent(x)
    ''')
    string_lines = qg.python_string_lines(f)
    # Lines 1+2 are inside the docstring; line 5 is the real call.
    assert 1 in string_lines
    assert 2 in string_lines
    assert 5 not in string_lines


# ── Guard: backend.no-print ──────────────────────────────────────────────────


def test_no_print_detects_new_violation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "leaky.py", """
        def f():
            print("oops")
    """)
    violations = qg._backend_no_print()
    assert any("leaky.py" in v.file.as_posix() for v in violations)


def test_no_print_clean_when_only_logger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "good.py", """
        import structlog
        logger = structlog.get_logger(__name__)
        def f():
            logger.info("ok")
    """)
    assert qg._backend_no_print() == []


# ── Guard: backend.analysis-router ───────────────────────────────────────────


def test_analysis_router_allows_owner_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``services/analysis_router.py`` is the dispatcher itself — direct
    engine calls there are required, not forbidden."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "analysis_router.py", """
        async def classify_test(...):
            return await run_triage_agent(payload)
    """)
    assert qg._backend_analysis_router_bypass() == []


def test_analysis_router_flags_router_bypass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "routers" / "shortcut.py", """
        from services.agent import run_triage_agent

        async def handle():
            return await run_triage_agent(payload)
    """)
    violations = qg._backend_analysis_router_bypass()
    assert len(violations) == 1
    assert "shortcut.py" in violations[0].file.as_posix()


# ── Guard: database.single-alembic-head ──────────────────────────────────────


def test_single_alembic_head_passes_for_linear_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    versions = tmp_path / "backend" / "migrations" / "versions"
    _write(versions / "0001.py", '''
        revision = "0001"
        down_revision = None
    ''')
    _write(versions / "0002.py", '''
        revision = "0002"
        down_revision = "0001"
    ''')
    assert qg._database_single_alembic_head() == []


def test_single_alembic_head_fails_for_forked_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    versions = tmp_path / "backend" / "migrations" / "versions"
    _write(versions / "0001.py", '''
        revision = "0001"
        down_revision = None
    ''')
    _write(versions / "0002a.py", '''
        revision = "0002a"
        down_revision = "0001"
    ''')
    _write(versions / "0002b.py", '''
        revision = "0002b"
        down_revision = "0001"
    ''')
    violations = qg._database_single_alembic_head()
    # Two heads → two violations.
    assert len(violations) == 2
    assert all("multiple Alembic heads" in v.message for v in violations)


# ── Guard: database.downgrade-implemented ────────────────────────────────────


def test_downgrade_implemented_passes_real_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "migrations" / "versions" / "0001_x.py", '''
        from alembic import op
        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.create_table("foo")

        def downgrade() -> None:
            op.drop_table("foo")
    ''')
    assert qg._database_downgrade_implemented() == []


def test_downgrade_implemented_flags_empty_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "migrations" / "versions" / "0001_x.py", '''
        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            pass

        def downgrade() -> None:
            pass
    ''')
    violations = qg._database_downgrade_implemented()
    assert len(violations) == 1
    assert "empty" in violations[0].message


def test_downgrade_implemented_accepts_comment_explanation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An intentional no-op is also accepted when the author has
    written an inline comment explaining it (the form used by
    migration 0045_clean_user_role_strings)."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "migrations" / "versions" / "0001_x.py", '''
        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.execute("UPDATE users SET role = REPLACE(role, 'X.', '')")

        def downgrade() -> None:
            # Intentionally a no-op — there's no reason to reintroduce the malformed data.
            pass
    ''')
    assert qg._database_downgrade_implemented() == []


def test_downgrade_implemented_accepts_docstring_only_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A downgrade that's intentionally a no-op should be allowed if
    the author has written a docstring explaining why."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "migrations" / "versions" / "0001_x.py", '''
        revision = "0001"
        down_revision = None

        def upgrade() -> None:
            op.execute("CREATE EXTENSION pg_trgm")

        def downgrade() -> None:
            """No-op: dropping the extension would break other migrations."""
    ''')
    assert qg._database_downgrade_implemented() == []


# ── Guard: agents.base-agent-subclass ────────────────────────────────────────


def test_base_agent_subclass_passes_proper_inheritance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "agents" / "foo_agent.py", """
        from app.agents.base import BaseAgent
        class FooAgent(BaseAgent):
            stage_name = "foo"
    """)
    assert qg._agents_base_agent_subclass() == []


def test_base_agent_subclass_flags_standalone_class(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "agents" / "rogue.py", """
        class RogueAgent:
            pass
    """)
    violations = qg._agents_base_agent_subclass()
    assert len(violations) == 1
    assert "rogue.py" in violations[0].file.as_posix()


# ── Guard: homelab.build-tag-placeholder ─────────────────────────────────────


def _write_homelab_overlay(tmp_path: Path, tags: dict[str, str | None]) -> Path:
    """Render a minimal overlay file whose ``images:`` block matches the
    real one's shape. ``tags`` maps image name → ``newTag`` value, or
    ``None`` to omit the ``newTag:`` line entirely (simulating the
    "missing newTag" failure mode).
    """
    rel = "k8s/overlays/homelab/kustomization.yaml"
    path = tmp_path / rel
    blocks: list[str] = []
    for name, tag in tags.items():
        block = f"  - name: {name}\n"
        block += f"    newName: registry.local:30500/{name}\n"
        if tag is not None:
            block += f"    newTag: {tag}\n"
        blocks.append(block)
    body = "images:\n" + "".join(blocks)
    _write(path, body)
    return path


# -- frontend.ai-output-hedging (US-15.1) ------------------------------------


def _write_tsx(tmp_path: Path, rel: str, body: str) -> Path:
    path = tmp_path / "frontend" / "src" / rel
    _write(path, body)
    return path


def test_ai_output_hedging_flags_bare_root_cause_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_tsx(tmp_path, "components/Bad.tsx", """
        export function Bad() {
          return <p>Root cause: the payment service returned 500.</p>
        }
    """)
    violations = qg._frontend_ai_output_hedging()
    assert [v.line for v in violations] == [2]
    assert "verdict" in violations[0].message


def test_ai_output_hedging_flags_assertive_causation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_tsx(tmp_path, "components/Bad2.tsx", """
        const copy = 'This failure is caused by a stale fixture.'
        const heading = 'Root Cause Summary'
        const line = 'We are confident the cause is a bad deploy.'
    """)
    lines = sorted(v.line for v in qg._frontend_ai_output_hedging())
    assert lines == [1, 2, 3]


def test_ai_output_hedging_allows_hedged_copy_and_stage_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_tsx(tmp_path, "components/Good.tsx", """
        const STAGES = [{ label: 'Root Cause Analysis' }]
        const heading = 'Suggested root cause'
        const hedge = 'Likely caused by a stale fixture - confirm before acting.'
        const caveat = 'Suspects, not culprits.'
    """)
    assert qg._frontend_ai_output_hedging() == []


def test_ai_output_hedging_ignores_comments_and_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    # A comment may quote the banned phrasing - that is how the guard and the
    # copy audit document themselves.
    _write_tsx(tmp_path, "components/Commented.tsx", """
        /* The old copy said the root cause is well-supported. */
        // Root cause: this line is a comment, not rendered copy.
        export const ok = 1
    """)
    _write_tsx(tmp_path, "components/Bad.test.tsx", """
        expect(screen.queryByText('Root cause:')).toBeNull()
    """)
    assert qg._frontend_ai_output_hedging() == []


def test_ai_output_hedging_preserves_line_numbers_after_block_comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_tsx(tmp_path, "components/Mixed.tsx", """
        /**
         * Multi-line header.
         */
        const copy = 'Root cause: everything.'
    """)
    violations = qg._frontend_ai_output_hedging()
    assert [v.line for v in violations] == [4]


def test_homelab_build_tag_placeholder_passes_when_all_placeholders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_homelab_overlay(tmp_path, {
        "testlookup/backend": "BUILD_TAG_PLACEHOLDER",
        "testlookup/frontend": "BUILD_TAG_PLACEHOLDER",
        "testlookup/mcp": "BUILD_TAG_PLACEHOLDER",
    })
    assert qg._homelab_build_tag_placeholder() == []


def test_homelab_build_tag_placeholder_fails_on_substituted_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The killer scenario: deploy-homelab.sh was killed mid-run after
    its sed but before its EXIT trap. The substituted timestamp tag got
    committed by accident."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_homelab_overlay(tmp_path, {
        "testlookup/backend": "build-20260518-013421",
        "testlookup/frontend": "BUILD_TAG_PLACEHOLDER",
        "testlookup/mcp": "BUILD_TAG_PLACEHOLDER",
    })
    violations = qg._homelab_build_tag_placeholder()
    assert len(violations) == 1
    assert "testlookup/backend" in violations[0].message
    assert "build-20260518-013421" in violations[0].message
    assert violations[0].line > 0  # points at the offending line


def test_homelab_build_tag_placeholder_fails_when_all_substituted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """All three images substituted = three violations (one per image)."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_homelab_overlay(tmp_path, {
        "testlookup/backend": "build-20260518-013421",
        "testlookup/frontend": "build-20260518-013421",
        "testlookup/mcp": "build-20260518-013421",
    })
    violations = qg._homelab_build_tag_placeholder()
    assert len(violations) == 3
    names = {v.message.split("'")[1] for v in violations}
    assert names == {"testlookup/backend", "testlookup/frontend", "testlookup/mcp"}


def test_homelab_build_tag_placeholder_flags_missing_image_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dropping one of the pinned images out of the overlay entirely
    must also fail — otherwise the cluster would silently fall back to
    whatever ``image:`` line lives in the base deployment, which could
    be a floating tag."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_homelab_overlay(tmp_path, {
        "testlookup/backend": "BUILD_TAG_PLACEHOLDER",
        # testlookup/frontend omitted entirely
        "testlookup/mcp": "BUILD_TAG_PLACEHOLDER",
    })
    violations = qg._homelab_build_tag_placeholder()
    assert len(violations) == 1
    assert "testlookup/frontend" in violations[0].message
    assert "missing 'images:' entry" in violations[0].message


def test_homelab_build_tag_placeholder_flags_missing_new_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An entry without any ``newTag:`` line at all — the cluster would
    inherit the base's tag, which is a regression we want to catch."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_homelab_overlay(tmp_path, {
        "testlookup/backend": None,  # no newTag line
        "testlookup/frontend": "BUILD_TAG_PLACEHOLDER",
        "testlookup/mcp": "BUILD_TAG_PLACEHOLDER",
    })
    violations = qg._homelab_build_tag_placeholder()
    assert len(violations) == 1
    assert "no newTag" in violations[0].message


def test_homelab_build_tag_placeholder_passes_when_overlay_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A repo that doesn't ship the homelab overlay shouldn't fail the
    guard — the overlay's optional. Forked / minimal checkouts must
    stay green."""
    _redirect_repo_root(monkeypatch, tmp_path)
    # Don't write k8s/overlays/homelab/kustomization.yaml at all.
    assert qg._homelab_build_tag_placeholder() == []


# ── Ratchet behaviour: baseline + new-violation diff ─────────────────────────


def _make_fake_guard(violations: list[qg.Violation]) -> qg.Guard:
    return qg.Guard(
        name="fake.guard",
        description="test guard",
        check=lambda: violations,
    )


def test_run_guard_passes_when_violations_are_in_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    v = qg.Violation(tmp_path / "x.py", 5, "boom")
    guard = _make_fake_guard([v])
    guard.save_baseline([v.key])

    ok = qg.run_guard(guard, update_baseline=False)
    assert ok is True
    captured = capsys.readouterr()
    assert "OK" in captured.out
    assert "FAIL" not in captured.out


def test_run_guard_fails_on_new_violation_not_in_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    v_known = qg.Violation(tmp_path / "old.py", 1, "old")
    v_new = qg.Violation(tmp_path / "new.py", 2, "new")
    guard = _make_fake_guard([v_known, v_new])
    guard.save_baseline([v_known.key])

    ok = qg.run_guard(guard, update_baseline=False)
    assert ok is False
    captured = capsys.readouterr()
    # The NEW violation must be reported; the baselined one must NOT
    # appear in the failure output.
    assert "new.py:2" in captured.out
    assert "old.py:1" not in captured.out


def test_run_guard_reports_stale_baseline_as_non_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the code is fixed but the baseline still lists the old
    entry, we nudge the developer to prune the baseline — but DO NOT
    fail the build. Stale baselines are a cleanup task, not a quality
    regression."""
    _redirect_repo_root(monkeypatch, tmp_path)
    guard = _make_fake_guard([])  # No current violations.
    # A well-formed fingerprint that no current violation produces.
    guard.save_baseline(["0" * 16])

    ok = qg.run_guard(guard, update_baseline=False)
    assert ok is True
    captured = capsys.readouterr()
    assert "stale" in captured.out.lower()


def test_update_baseline_rewrites_file_to_current_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    v = qg.Violation(tmp_path / "a.py", 7, "x")
    guard = _make_fake_guard([v])

    ok = qg.run_guard(guard, update_baseline=True)
    assert ok is True
    # load_baseline maps key -> human annotation; the keys are the contract.
    assert set(guard.load_baseline()) == {v.key}


# ── Baseline keys: content fingerprints, not line numbers ────────────────────
#
# The scheme these pin exists because the old `<relpath>:<lineno>` key made
# every guard hostage to line drift. On 2026-08-21 the SAME untouched
# `run_triage_agent(` call in analysis_agent.py failed CI twice in one session
# (631 -> 642 -> 684) purely because unrelated code was added above it, and the
# fix both times was to bump a number in a baseline file. That is the habit
# worth killing: bump-without-reading is how a real new violation gets waved
# through.


def _no_print_guard() -> qg.Guard:
    """A guard over the real `backend.no-print` check, so these tests exercise
    the whole path (walk -> violation -> key -> baseline file) rather than the
    hash function alone."""
    return qg.Guard(
        name="fake.no-print",
        description="print() in backend/app",
        check=qg._backend_no_print,
    )


def test_inserting_a_line_above_a_tolerated_violation_is_not_a_new_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE regression this scheme exists to prevent."""
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        import structlog

        logger = structlog.get_logger(__name__)


        def debug_dump(payload):
            print("tolerated on purpose")
            return payload
    """)
    guard = _no_print_guard()
    qg.run_guard(guard, update_baseline=True)
    before = set(guard.load_baseline())
    assert len(before) == 1, "expected exactly one tolerated print()"

    # Insert lines ABOVE the tolerated print(). Nothing about the print
    # changed -- only its line number did. This is the exact shape of the
    # 631 -> 642 -> 684 drift.
    body = target.read_text(encoding="utf-8")
    target.write_text("# a new module comment" + chr(10) * 3 + body, encoding="utf-8")

    capsys.readouterr()  # drop the --update-baseline chatter
    ok = qg.run_guard(guard, update_baseline=False)
    out = capsys.readouterr().out

    assert ok is True
    assert "FAIL" not in out
    assert "stale" not in out.lower()
    # And nothing needed rewriting: the baseline file is untouched.
    assert set(guard.load_baseline()) == before


def test_reindenting_a_tolerated_line_is_not_a_new_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Whitespace is normalized out of the key, so wrapping the call in an
    `if` (which re-indents it) does not cost a baseline churn."""
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        def debug_dump(payload):
            print("tolerated on purpose")
    """)
    loose = qg._backend_no_print()[0].key

    _write(target, """
        def debug_dump(payload):
                print("tolerated on purpose")
    """)
    assert qg._backend_no_print()[0].key == loose


def test_editing_the_tolerated_line_does_re_report_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the contract. An exemption is granted for specific
    code; change the code and it is re-reviewed, not inherited."""
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        def debug_dump(payload):
            print("tolerated on purpose")
    """)
    guard = _no_print_guard()
    qg.run_guard(guard, update_baseline=True)

    _write(target, """
        def debug_dump(payload):
            print(f"leaking {payload}")
    """)
    capsys.readouterr()
    ok = qg.run_guard(guard, update_baseline=False)
    out = capsys.readouterr().out

    assert ok is False
    assert "FAIL" in out
    # ...and the entry it replaced is called out as stale, by location.
    assert "stale" in out.lower()
    assert "legacy.py" in out


def test_moving_the_call_to_another_function_does_re_report_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The enclosing function is part of the key: the reviewer approved a call
    in a named place, not the string anywhere in the file."""
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        def debug_dump(payload):
            print("tolerated on purpose")

        def ship_it(payload):
            return payload
    """)
    before = qg._backend_no_print()[0].key

    _write(target, """
        def debug_dump(payload):
            return payload

        def ship_it(payload):
            print("tolerated on purpose")
    """)
    assert qg._backend_no_print()[0].key != before


def test_enclosing_scope_is_the_innermost_def_and_carries_its_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        import functools

        class Reporter:
            @functools.cached_property
            def summary(self):
                print("inside a decorated method")
                return 1

        def free_function():
            print("module level def")
    """)
    by_line = {v.line: v.scope for v in qg._backend_no_print()}
    assert by_line[6] == "Reporter.summary"
    assert by_line[10] == "free_function"


def test_identical_lines_in_one_scope_keep_separate_baseline_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two byte-identical matches in one function must not collapse into one
    key -- baselining the first would silently tolerate the second."""
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        def debug_dump(payload):
            print("same")
            print("same")
    """)
    violations = qg.assign_occurrences(qg._backend_no_print())
    assert len(violations) == 2
    assert len({v.key for v in violations}) == 2

    guard = _no_print_guard()
    guard.save_baseline([violations[0]])
    capsys.readouterr()
    ok = qg.run_guard(guard, update_baseline=False)
    assert ok is False
    assert "FAIL" in capsys.readouterr().out


def test_file_level_violations_key_on_the_path_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`line=0` means "this file, as a whole" (e.g. "never calls finalize_run").
    Those were already drift-proof and stay keyed on the path -- including the
    long-standing behaviour that two file-level findings on one path share an
    entry, exactly as `<path>:0` did."""
    _redirect_repo_root(monkeypatch, tmp_path)
    a = qg.Violation(tmp_path / "backend" / "app" / "x.py", 0, "missing X")
    b = qg.Violation(tmp_path / "backend" / "app" / "x.py", 0, "a different message")
    c = qg.Violation(tmp_path / "backend" / "app" / "y.py", 0, "missing X")
    assert a.key == b.key
    assert a.key != c.key
    # Path-only entries carry the path as their annotation, with no line.
    assert a.annotation == "backend/app/x.py"


def test_a_missing_file_still_produces_a_stable_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guards report violations against files that do not exist ("expected
    ingestion owner missing"). Reading source for the key must not raise."""
    _redirect_repo_root(monkeypatch, tmp_path)
    v = qg.Violation(tmp_path / "backend" / "app" / "gone.py", 12, "missing")
    assert len(v.key) == 16
    assert v.format() == "backend/app/gone.py:12: missing"


# ── Baseline file: annotations, preserved notes, legacy entries ──────────────


def test_baseline_entries_carry_a_readable_annotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hash nobody can read is a hash nobody reviews. Every entry records
    where it came from, as a comment the loader ignores."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "legacy.py", """
        def debug_dump(payload):
            print("tolerated on purpose")
    """)
    guard = _no_print_guard()
    qg.run_guard(guard, update_baseline=True)
    text = guard.baseline_path.read_text(encoding="utf-8")

    assert "backend/app/legacy.py:2" in text
    assert "in debug_dump" in text
    assert 'print("tolerated on purpose")' in text
    # The annotation is a comment: it must not leak into the matched keys.
    assert all(qg._BASELINE_KEY_RE.match(k) for k in guard.load_baseline())


def test_update_baseline_preserves_handwritten_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two real baselines carry paragraphs explaining WHY their entries are
    tolerated (GIT-001; "empty on purpose"). Regenerating must not eat them."""
    _redirect_repo_root(monkeypatch, tmp_path)
    guard = _make_fake_guard([qg.Violation(tmp_path / "a.py", 0, "x")])
    guard.baseline_path.parent.mkdir(parents=True, exist_ok=True)
    guard.baseline_path.write_text(
        "# Auto-generated by scripts/quality_gate.py --update-baseline." + chr(10)
        + "# Each line is a tolerated <relpath>:<lineno> match for this guard." + chr(10)
        + "# Reviewed in PR; remove entries as the code is cleaned up." + chr(10)
        + "#" + chr(10)
        + "# WHY: owner-approved exemption, see TICKET-7." + chr(10)
        + "a.py:0" + chr(10),
        encoding="utf-8",
    )

    qg.run_guard(guard, update_baseline=True)
    text = guard.baseline_path.read_text(encoding="utf-8")
    assert "# WHY: owner-approved exemption, see TICKET-7." in text
    # The superseded boilerplate is dropped, not stacked.
    assert text.count("Auto-generated by scripts/quality_gate.py") == 1
    assert "<relpath>:<lineno> match for this guard" not in text

    # Round-trips: a second regeneration neither drops nor duplicates the note.
    qg.run_guard(guard, update_baseline=True)
    text2 = guard.baseline_path.read_text(encoding="utf-8")
    assert text2.count("# WHY: owner-approved exemption, see TICKET-7.") == 1
    assert text2 == text


def test_legacy_line_keyed_entries_are_reported_not_silently_honoured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A branch cut before this change still has `<path>:<lineno>` entries.
    They tolerate nothing now, so the gate must say why rather than reporting
    a pile of unexplained new violations."""
    _redirect_repo_root(monkeypatch, tmp_path)
    v = qg.Violation(tmp_path / "a.py", 3, "boom")
    guard = _make_fake_guard([v])
    guard.baseline_path.parent.mkdir(parents=True, exist_ok=True)
    guard.baseline_path.write_text("a.py:3" + chr(10), encoding="utf-8")

    ok = qg.run_guard(guard, update_baseline=False)
    out = capsys.readouterr().out
    assert ok is False
    assert "legacy" in out.lower()
    assert "--update-baseline" in out


def test_stale_entry_names_what_it_was_tolerating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pruning a stale hash is only possible if the gate says what it was."""
    _redirect_repo_root(monkeypatch, tmp_path)
    target = tmp_path / "backend" / "app" / "legacy.py"
    _write(target, """
        def debug_dump(payload):
            print("tolerated on purpose")
    """)
    guard = _no_print_guard()
    qg.run_guard(guard, update_baseline=True)

    _write(target, """
        def debug_dump(payload):
            return payload
    """)
    capsys.readouterr()
    ok = qg.run_guard(guard, update_baseline=False)
    out = capsys.readouterr().out
    assert ok is True  # stale is a cleanup nudge, never a build failure
    assert "stale" in out.lower()
    assert "backend/app/legacy.py:2" in out
    assert "in debug_dump" in out


def test_update_baseline_does_not_invent_a_file_for_a_clean_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Several guards ship at zero with NO baseline file -- absolute rules, not
    ratchets. A repo-wide --update-baseline must leave them that way."""
    _redirect_repo_root(monkeypatch, tmp_path)
    guard = _make_fake_guard([])
    qg.run_guard(guard, update_baseline=True)
    assert not guard.baseline_path.exists()

    # An existing file, however, is kept and rewritten even when it empties out
    # -- that is how a ratchet reaches zero without losing its notes.
    guard.baseline_path.parent.mkdir(parents=True, exist_ok=True)
    guard.baseline_path.write_text("# keep me" + chr(10), encoding="utf-8")
    qg.run_guard(guard, update_baseline=True)
    assert guard.baseline_path.exists()
    assert "# keep me" in guard.baseline_path.read_text(encoding="utf-8")


def test_committed_baselines_are_all_fingerprints() -> None:
    """Runs against the REAL scripts/quality-gate-baselines/. A half-migrated
    file would tolerate nothing and fail CI with no explanation."""
    files = sorted(qg.BASELINE_DIR.glob("*.txt"))
    assert files, "expected committed baseline files"
    for path in files:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            entry = raw.partition("#")[0].strip()
            if entry:
                assert qg._BASELINE_KEY_RE.match(entry), (
                    f"{path.name}:{lineno} is not a fingerprint: {entry!r}. "
                    f"Regenerate with --update-baseline."
                )


# ── DEVELOPER_GUIDE.md sync ───────────────────────────────────────────────
#
# The guide's section 1 is hand-maintained prose over an auto-listable
# registry, so it drifts the moment a guard is added: it claimed "18 guards"
# while the registry ran 26, and nine guards had no table row at all. These
# two pin it to the registry itself.


_GUIDE = "architecture/DEVELOPER_GUIDE.md"
_DAGGER = chr(0x2020)  # marks an absolute rule (no baseline) in the guide


def _guide_guard_rows(doc: str) -> dict[str, bool]:
    """Gate id -> "is it flagged as an absolute rule", for every guard row in
    the guide's per-surface tables. Rows read ``| `backend.no-print` | ... |``
    and an absolute rule carries a trailing dagger after the id."""
    rows = re.findall(r"^\| `([a-z]+\.[a-z0-9-]+)` *(" + _DAGGER + r"?)", doc, re.M)
    return {name: bool(mark) for name, mark in rows}


def test_developer_guide_documents_every_guard() -> None:
    """Runs against the REAL architecture/DEVELOPER_GUIDE.md. A stated count
    that nobody bumped is worse than no count: it reads as authoritative."""
    doc = (qg.REPO_ROOT / _GUIDE).read_text(encoding="utf-8")

    stated = re.search(r"enforces \*\*(\d+) guards\*\*", doc)
    assert stated, f"{_GUIDE} no longer states a guard count in section 1"
    assert int(stated.group(1)) == len(qg.GUARDS), (
        f"{_GUIDE} says {stated.group(1)} guards; the registry has "
        f"{len(qg.GUARDS)}. Run `python scripts/quality_gate.py --list` and "
        f"update section 1 (count AND the per-surface table)."
    )

    documented = set(_guide_guard_rows(doc))
    registered = {g.name for g in qg.GUARDS}
    assert documented == registered, (
        f"{_GUIDE} guard tables are out of sync with GUARDS. "
        f"Undocumented: {sorted(registered - documented)}. "
        f"Stale rows: {sorted(documented - registered)}."
    )


def test_developer_guide_marks_absolute_rules_not_ratchets() -> None:
    """A guard with no baseline file fails on the FIRST violation; a ratchet
    tolerates everything already listed. Documenting one as the other tells a
    reader the opposite of what CI will do to them."""
    doc = (qg.REPO_ROOT / _GUIDE).read_text(encoding="utf-8")
    rows = _guide_guard_rows(doc)

    for guard in qg.GUARDS:
        absolute = not guard.baseline_path.exists()
        kind = "an absolute rule" if absolute else "a ratchet"
        assert rows.get(guard.name) == absolute, (
            f"{guard.name} is {kind} (baseline file "
            f"{'absent' if absolute else 'present'}) but {_GUIDE} marks it as "
            f"the opposite. {_DAGGER} = absolute rule, no marker = ratchet."
        )

    absolutes = sum(1 for g in qg.GUARDS if not g.baseline_path.exists())
    assert f"{len(qg.GUARDS) - absolutes} are *ratchets*" in doc, (
        f"{_GUIDE} should open section 1 with the ratchet count "
        f"({len(qg.GUARDS) - absolutes})."
    )
    assert f"The other {absolutes} ship at zero" in doc, (
        f"{_GUIDE} should name the absolute-rule count ({absolutes})."
    )


# ── repo.no-gitignored-source (GIT-001) ──────────────────────────────────────
#
# The guard shells out to `git check-ignore`, which is the authority on
# gitignore semantics. These tests pin the parts that are OURS: the two
# "could not look" bailouts, and the tracked/untracked wording. The guard's
# behaviour against real files is exercised end to end by running the gate in
# a scratch tree with an offending filename present.


def _fake_completed(stdout: bytes = b"", returncode: int = 1, stderr: bytes = b""):
    class _R:
        pass

    r = _R()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def test_gitignored_source_bails_out_when_the_scan_finds_almost_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Zero violations must mean "clean", never "the walk broke".

    Without this, moving or renaming a source root turns the guard into a
    permanent green light — the same fail-open shape as a check that passes
    because it could not look.
    """
    _redirect_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(qg, "_source_candidates", lambda: ["backend/app/a.py"])

    violations = qg._repo_no_gitignored_source()

    assert len(violations) == 1
    assert "cannot have looked" in violations[0].message


def test_gitignored_source_treats_a_git_failure_as_a_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """check-ignore exits 0 (matched) or 1 (no match). Anything else is git
    failing, and a failed command must not read as a pass."""
    _redirect_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(qg, "_source_candidates", lambda: [f"f{i}.py" for i in range(200)])
    monkeypatch.setattr(
        qg.subprocess, "run",
        lambda *a, **k: _fake_completed(returncode=128, stderr=b"fatal: not a git repository"),
    )

    violations = qg._repo_no_gitignored_source()

    assert len(violations) == 1
    assert "could not look" in violations[0].message


def test_gitignored_source_distinguishes_untracked_from_tracked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two cases carry different urgency and must not share wording.

    An UNTRACKED match is the live incident — the next `git add` drops it.
    A TRACKED match is a landmine: it works until someone recreates the file.
    """
    _redirect_repo_root(monkeypatch, tmp_path)
    monkeypatch.setattr(qg, "_source_candidates", lambda: [f"f{i}.py" for i in range(200)])
    monkeypatch.setattr(
        qg.subprocess, "run",
        lambda *a, **k: _fake_completed(
            returncode=0,
            stdout=(
                b".gitignore:15:*credentials*\tbackend/app/services/new_credentials.py\n"
                b".gitignore:27:*api_key*\tbackend/app/routers/api_keys.py\n"
            ),
        ),
    )
    monkeypatch.setattr(qg, "_git_tracked", lambda paths: {"backend/app/routers/api_keys.py"})

    violations = qg._repo_no_gitignored_source()
    by_path = {v.file.name: v.message for v in violations}

    assert "UNTRACKED" in by_path["new_credentials.py"]
    assert "git add` will skip it" in by_path["new_credentials.py"]
    assert "UNTRACKED" not in by_path["api_keys.py"]
    assert "already tracked" in by_path["api_keys.py"]


def test_gitignored_source_roots_cover_tests_not_just_app_code() -> None:
    """The first real incident was a TEST file.

    ``test_no_shared_default_credentials.py`` was silently excluded and a
    security fix landed unguarded. A roots tuple covering only application
    code misses the exact case that motivated this guard — verified by
    reproducing incident 1 against an app-only tuple, which did not trip.
    """
    assert "backend/tests" in qg._SOURCE_ROOTS
    assert "backend/app" in qg._SOURCE_ROOTS
    assert "frontend/src" in qg._SOURCE_ROOTS
    # Migrations too: a lost revision file breaks `alembic upgrade head`
    # for every deployment, and 0056_api_key_project_scope.py matches.
    assert "backend/migrations" in qg._SOURCE_ROOTS


# ── backend.streaming-body-not-rebound ───────────────────────────────────────


def test_streaming_body_flags_the_as_rebinding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exact shape that shipped broken in S3 ``stream_object`` (#824)."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "db" / "storage.py", '''
        async def stream_object(self, key):
            async with self.get_client_context() as s3:
                response = await s3.get_object(Bucket="b", Key=key)
                async with response["Body"] as stream:
                    while chunk := await stream.read(65536):
                        yield chunk
    ''')

    violations = qg._backend_streaming_body_not_rebound()

    assert len(violations) == 1
    assert "StreamingBody proxy" in violations[0].message


def test_streaming_body_allows_the_bound_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Entering the context is required; only the ``as`` is forbidden."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "db" / "storage.py", '''
        async def stream_object(self, key):
            async with self.get_client_context() as s3:
                response = await s3.get_object(Bucket="b", Key=key)
                body = response["Body"]
                async with body:
                    async for chunk in body.iter_chunks(65536):
                        yield chunk
    ''')

    assert qg._backend_streaming_body_not_rebound() == []


def test_streaming_body_flags_the_no_argument_read_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``get_object_content``'s shape WORKS, and must still be flagged.

    A no-argument ``read()`` is valid on ClientResponse, so this variant
    returns correct bytes — verified byte-identical against live MinIO. That
    is exactly why no behavioural test can guard it: it is a loaded trap that
    fires only when someone later passes a size. The guard must not wait for
    the failing variant.
    """
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "db" / "storage.py", '''
        async def get_object_content(self, key):
            async with self.get_client_context() as s3:
                response = await s3.get_object(Bucket="b", Key=key)
                async with response["Body"] as stream:
                    return await stream.read()
    ''')

    assert len(qg._backend_streaming_body_not_rebound()) == 1


def test_streaming_body_ignores_unrelated_context_managers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a ``["Body"]`` subscript is the trap — do not cry wolf."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "svc.py", '''
        async def go(session, payloads):
            async with session.get(url) as resp:
                data = await resp.json()
            with open(path) as fh:
                fh.read()
            async with payloads["Header"] as h:
                await h.read()
            return data
    ''')

    assert qg._backend_streaming_body_not_rebound() == []
