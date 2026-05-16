"""Tests for ``scripts/quality_gate.py``.

These pin the ratchet contract: a guard with a baseline tolerates the
listed entries and ONLY fails on new violations. They run without any
backend / frontend deps — pure file IO + the script's own helpers.

Run from the repo root::

    python -m pytest scripts/test_quality_gate.py -v

The tests stand on their own (no need for the backend test rig).
"""
from __future__ import annotations

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
    guard.save_baseline(["old/file.py:1"])

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
    assert guard.load_baseline() == {v.key}
