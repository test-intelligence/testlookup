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
