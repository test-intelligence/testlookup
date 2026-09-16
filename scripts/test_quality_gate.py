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
import subprocess
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


def test_base_agent_subclass_ignores_workflow_compiler_support_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "agents" / "workflow_compiler.py", """
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class WorkflowValidation:
            valid: bool
    """)
    assert qg._agents_base_agent_subclass() == []


# ── Guard: frontend.ingest-formats-match-backend ─────────────────────────────


def _write_ingest_registries(
    tmp_path: Path, *, backend: list[str], frontend: list[str]
) -> None:
    """Craft the two registry files the parity guard reads: the backend
    ``_SUPPORTED_FORMATS`` set in ``routers/ingest.py`` and the frontend
    ``SUPPORTED_FORMATS`` array in ``reportUploadService.ts``."""
    backend_body = ", ".join(f'"{f}"' for f in backend)
    _write(tmp_path / "backend" / "app" / "routers" / "ingest.py", f"""
        _SUPPORTED_FORMATS = {{
            {backend_body},
        }}
    """)
    frontend_rows = "\n".join(
        f"  {{ value: '{f}', label: '{f}' }}," for f in frontend
    )
    _write(tmp_path / "frontend" / "src" / "services" / "reportUploadService.ts", f"""
        export type ReportFormat = 'auto'
        export const SUPPORTED_FORMATS: ReadonlyArray<{{ value: ReportFormat; label: string }}> = [
        {frontend_rows}
        ]
    """)


def test_ingest_formats_match_when_aligned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    fmts = ["auto", "junit", "allure", "playwright"]
    _write_ingest_registries(tmp_path, backend=fmts, frontend=fmts)
    assert qg._ingest_formats_match_ui() == []


def test_ingest_formats_flags_ui_advertising_unaccepted_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_ingest_registries(
        tmp_path,
        backend=["auto", "junit"],
        frontend=["auto", "junit", "cypress"],
    )
    violations = qg._ingest_formats_match_ui()
    assert [v.file.name for v in violations] == ["reportUploadService.ts"]
    assert "cypress" in violations[0].message


def test_ingest_formats_flags_backend_accepting_unadvertised_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_ingest_registries(
        tmp_path,
        backend=["auto", "junit", "robot"],
        frontend=["auto", "junit"],
    )
    violations = qg._ingest_formats_match_ui()
    assert [v.file.name for v in violations] == ["ingest.py"]
    assert "robot" in violations[0].message


def test_ingest_formats_flags_unparseable_backend_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A registry refactor must not silently disable parity enforcement.
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "routers" / "ingest.py", """
        SUPPORTED = ("auto", "junit")  # renamed, no _SUPPORTED_FORMATS anchor
    """)
    _write(tmp_path / "frontend" / "src" / "services" / "reportUploadService.ts", """
        export const SUPPORTED_FORMATS: ReadonlyArray<{ value: string; label: string }> = [
          { value: 'auto', label: 'Auto-detect' },
        ]
    """)
    violations = qg._ingest_formats_match_ui()
    assert [v.file.name for v in violations] == ["ingest.py"]
    assert "cannot parse" in violations[0].message


def test_ingest_formats_flags_unparseable_frontend_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "routers" / "ingest.py", """
        _SUPPORTED_FORMATS = {"auto", "junit"}
    """)
    _write(tmp_path / "frontend" / "src" / "services" / "reportUploadService.ts", """
        export const FORMATS = ['auto', 'junit']
    """)
    violations = qg._ingest_formats_match_ui()
    assert [v.file.name for v in violations] == ["reportUploadService.ts"]
    assert "cannot parse" in violations[0].message


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


# -- Guard: backend.metrics-are-emitted ---------------------------------------
#
# A declared-but-never-incremented Prometheus metric is worse than a missing
# one: an unlabelled counter exports a confident ``0.0`` (which reads as a
# measured zero) and a labelled one exports no series at all, so every panel
# and alert written against it is silently, permanently empty.


def _metrics_repo(tmp_path: Path, decls: str, emitters: str = "") -> None:
    _write(tmp_path / "backend" / "app" / "core" / "metrics.py", decls)
    if emitters:
        _write(tmp_path / "backend" / "app" / "services" / "thing.py", emitters)


def test_metrics_are_emitted_flags_a_declared_but_dead_metric(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _metrics_repo(tmp_path, """
        from prometheus_client import Counter
        widgets_total = Counter("widgets_total", "Widgets", ["status"])
    """)
    violations = qg._backend_metrics_are_emitted()
    assert [v.line for v in violations] == [2]
    assert "widgets_total" in violations[0].message


def test_metrics_are_emitted_passes_when_production_code_emits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _metrics_repo(tmp_path, """
        from prometheus_client import Counter
        widgets_total = Counter("widgets_total", "Widgets", ["status"])
    """, """
        from app.core.metrics import widgets_total
        def f():
            widgets_total.labels(status="ok").inc()
    """)
    assert qg._backend_metrics_are_emitted() == []


def test_metrics_are_emitted_does_not_accept_a_test_only_emitter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A metric exercised only by its own unit test is still zero in
    production -- which is exactly the state this guard exists to catch."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _metrics_repo(tmp_path, """
        from prometheus_client import Counter
        widgets_total = Counter("widgets_total", "Widgets", ["status"])
    """)
    _write(tmp_path / "backend" / "tests" / "test_widgets.py", """
        from app.core.metrics import widgets_total
        def test_it():
            widgets_total.labels(status="ok").inc()
    """)
    assert len(qg._backend_metrics_are_emitted()) == 1


def test_metrics_are_emitted_does_not_credit_a_longer_neighbour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_`` is a word character, so without a leading \b the emitter search
    for ``uploads_total`` would match ``report_uploads_total.inc()`` and pass a
    genuinely dead metric."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _metrics_repo(tmp_path, """
        from prometheus_client import Counter
        uploads_total = Counter("uploads_total", "Uploads")
        report_uploads_total = Counter("report_uploads_total", "Report uploads")
    """, """
        from app.core.metrics import report_uploads_total
        def f():
            report_uploads_total.inc()
    """)
    violations = qg._backend_metrics_are_emitted()
    assert len(violations) == 1
    assert "uploads_total is declared" in violations[0].message


# -- Guard: backend.managed-test-case-status-single-writer --------------------


def _managed_status_repo(tmp_path: Path, relative: str, source: str) -> None:
    _write(tmp_path / "backend" / "app" / relative, source)


def test_managed_status_single_writer_flags_direct_assignment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _managed_status_repo(tmp_path, "services/legacy.py", """
        from app.models.postgres import ManagedTestCase
        async def deprecate(db, case_id):
            case = await db.get(ManagedTestCase, case_id)
            case.status = "deprecated"
    """)
    violations = qg._backend_managed_test_case_status_single_writer()
    assert len(violations) == 1
    assert violations[0].line == 4
    assert "outside the lifecycle service" in violations[0].message


def test_managed_status_single_writer_flags_dynamic_setattr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _managed_status_repo(tmp_path, "routers/test_management_shared.py", """
        from app.models.postgres import ManagedTestCase
        def apply_model_updates(model_instance, values):
            for field, value in values.items():
                setattr(model_instance, field, value)
    """)
    violations = qg._backend_managed_test_case_status_single_writer()
    assert len(violations) == 1
    assert "status-capable setattr" in violations[0].message


def test_managed_status_single_writer_accepts_explicit_setattr_denylist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _managed_status_repo(tmp_path, "routers/test_management_shared.py", """
        from app.models.postgres import ManagedTestCase
        def apply_model_updates(model_instance, values):
            for field, value in values.items():
                if field == "status":
                    continue
                setattr(model_instance, field, value)
    """)
    assert qg._backend_managed_test_case_status_single_writer() == []


def test_managed_status_single_writer_allows_initial_constructor_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _managed_status_repo(tmp_path, "services/factory.py", """
        from app.models.postgres import ManagedTestCase
        def create(project_id):
            return ManagedTestCase(project_id=project_id, status="draft")
    """)
    assert qg._backend_managed_test_case_status_single_writer() == []


def test_managed_status_single_writer_allows_lifecycle_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _managed_status_repo(tmp_path, "services/test_case_lifecycle_service.py", """
        from app.models.postgres import ManagedTestCase
        async def transition(case: ManagedTestCase, target):
            case.status = target
    """)
    assert qg._backend_managed_test_case_status_single_writer() == []


def test_managed_status_single_writer_ignores_other_status_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _managed_status_repo(tmp_path, "services/reviews.py", """
        from app.models.postgres import ManagedTestCase, TestCaseReview
        def finish(review: TestCaseReview):
            review.status = "approved"
    """)
    assert qg._backend_managed_test_case_status_single_writer() == []


# ── workflows.builtins-match-compiled ────────────────────────────────────


def _write_workflow_parity_test(tmp_path: Path, *, body: str | None = None) -> Path:
    target = tmp_path / "backend" / "tests" / "test_workflow_compiler.py"
    _write(target, body or '''
        import pytest

        @pytest.mark.parametrize(
            ("workflow_id", "legacy_builder"),
            [
                ("offline", workflow._build_offline_graph),
                ("deep", workflow._build_deep_graph),
                ("live", workflow._build_live_graph),
            ],
        )
        def test_builtin_definitions_compile_to_the_live_graph_topology(
            workflow_id, legacy_builder,
        ):
            body = definitions.body_from_item(definitions.builtin(workflow_id))
            compiled = compile_workflow(
                body, node_executors=workflow.workflow_node_executors()
            )
            assert _topology(compiled.graph) == _topology(legacy_builder())
    ''')
    return target


def test_workflow_builtin_guard_accepts_full_three_way_parity_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write_workflow_parity_test(tmp_path)
    assert qg._workflows_builtins_match_compiled() == []


def test_workflow_builtin_guard_covers_the_real_repository() -> None:
    assert qg._workflows_builtins_match_compiled() == []


def test_workflow_builtin_guard_rejects_missing_builtin_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    path = _write_workflow_parity_test(tmp_path)
    source = path.read_text(encoding="utf-8").replace(
        '("live", workflow._build_live_graph),', ""
    )
    path.write_text(source, encoding="utf-8")

    violations = qg._workflows_builtins_match_compiled()
    assert len(violations) == 1
    assert "exactly offline/deep/live" in violations[0].message


def test_workflow_builtin_guard_rejects_duplicate_builtin_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    path = _write_workflow_parity_test(tmp_path)
    source = path.read_text(encoding="utf-8").replace(
        '("live", workflow._build_live_graph),',
        '("live", workflow._build_live_graph),\n'
        '        ("live", workflow._build_live_graph),',
    )
    path.write_text(source, encoding="utf-8")

    violations = qg._workflows_builtins_match_compiled()
    assert len(violations) == 1
    assert "exactly offline/deep/live" in violations[0].message


def test_workflow_builtin_guard_rejects_fake_executors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    path = _write_workflow_parity_test(tmp_path)
    source = path.read_text(encoding="utf-8").replace(
        "workflow.workflow_node_executors()", "{}"
    )
    path.write_text(source, encoding="utf-8")

    violations = qg._workflows_builtins_match_compiled()
    assert len(violations) == 1
    assert "real workflow node executors" in violations[0].message


def test_workflow_builtin_guard_rejects_nodes_only_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    path = _write_workflow_parity_test(tmp_path)
    source = path.read_text(encoding="utf-8").replace(
        "_topology(compiled.graph) == _topology(legacy_builder())",
        "set(compiled.graph.nodes) == set(legacy_builder().nodes)",
    )
    path.write_text(source, encoding="utf-8")

    violations = qg._workflows_builtins_match_compiled()
    assert len(violations) == 1
    assert "full node, edge, and branch topology" in violations[0].message


def test_workflow_builtin_guard_rejects_skipped_parity_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    path = _write_workflow_parity_test(tmp_path)
    source = path.read_text(encoding="utf-8").replace(
        "def test_builtin_definitions_compile_to_the_live_graph_topology(",
        "@pytest.mark.skip(reason='too slow')\n"
        "def test_builtin_definitions_compile_to_the_live_graph_topology(",
    )
    path.write_text(source, encoding="utf-8")

    violations = qg._workflows_builtins_match_compiled()
    assert len(violations) == 1
    assert "must not be skipped" in violations[0].message


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


def test_gitignored_source_baseline_has_no_stale_entries() -> None:
    """The committed baseline must list only violations that still fire.

    A stale entry — a fingerprint the guard no longer produces — is not a
    failure, so the gate only prints a `!!` nudge and passes. Left alone it
    prints on every run forever, and a permanent yellow line trains reviewers
    to tune out gate warnings. Five camelCase sources (``useApiKeys.ts``,
    ``ApiKeysPage.tsx``, ``apiKeyService.ts``, ``apiKey.ts``,
    ``ParallelSuitesWithOneApiKeyTest.java``) sat exactly like that: they spell
    the feature ``ApiKey`` / ``apiKey``, which contains neither the lowercase
    substring ``api_key`` nor ``apikey``, so the globs never matched them on a
    case-sensitive filesystem — ``git check-ignore`` is the authority and on
    Linux CI does not flag them. This pins the baseline at zero stale entries
    so that drift cannot re-accumulate unnoticed.

    Unlike the sibling GIT-001 tests this runs against the real tree, not a
    tmp fixture: staleness is a property of the *committed* baseline versus the
    *current* repo, and only the real ``git check-ignore`` can decide it. The
    CI quality-gate job is a full ``actions/checkout`` with git available, the
    same context the gate itself relies on.
    """
    guard = next(g for g in qg.GUARDS if g.name == "repo.no-gitignored-source")
    baseline = guard.load_baseline()
    fingerprints = {k for k in baseline if qg._BASELINE_KEY_RE.match(k)}
    live_keys = {v.key for v in qg._repo_no_gitignored_source()}

    stale = fingerprints - live_keys
    assert not stale, (
        "repo.no-gitignored-source baseline carries stale entries the guard no "
        "longer produces — prune with `python scripts/quality_gate.py --only "
        "repo.no-gitignored-source --update-baseline`. Stale: "
        + ", ".join(sorted(baseline.get(k) or k for k in stale))
    )

    # The three that remain are the genuine lowercase-`api_key` matches; the
    # camelCase sources must not creep back in.
    annotations = " ".join(baseline.values())
    assert "backend/app/routers/api_keys.py" in annotations
    for pruned in ("useApiKeys.ts", "ApiKeysPage.tsx", "apiKeyService.ts", "apiKey.ts"):
        assert pruned not in annotations, (
            f"{pruned} is back in the baseline; it never matches the lowercase "
            "glob on a case-sensitive filesystem and must not be baselined."
        )


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


# ── GIT-001: the verdict must not depend on the developer's filesystem ───────


def _check_ignore(paths: list[str], *, force_case_sensitive: bool) -> set[str]:
    """Ask git which of ``paths`` match an ignore rule, the way the guard does."""
    argv = ["git"]
    if force_case_sensitive:
        argv += ["-c", "core.ignorecase=false"]
    argv += ["check-ignore", "--stdin", "-v", "--no-index"]
    proc = subprocess.run(
        argv, cwd=qg.REPO_ROOT,
        input=b"\n".join(p.encode() for p in paths),
        capture_output=True,
    )
    assert proc.returncode in (0, 1), proc.stderr.decode(errors="replace")[:400]
    return {
        line.rsplit("\t", 1)[-1]
        for line in proc.stdout.decode(errors="replace").splitlines()
        if "\t" in line
    }


# The camelCase API-key sources. `*apikey*` is lowercase, so a case-SENSITIVE
# match (Linux CI) misses them and a case-INSENSITIVE one (a Windows checkout,
# where core.ignorecase defaults to true) hits them.
_CAMEL_CASE_SOURCES = [
    "frontend/src/hooks/useApiKeys.ts",
    "frontend/src/pages/settings/ApiKeysPage.tsx",
    "frontend/src/services/apiKeyService.ts",
    "frontend/src/types/apiKey.ts",
]
# A genuine lowercase match, which must stay caught on every platform.
_LOWERCASE_SOURCE = "backend/app/services/api_key_service.py"


def test_the_guard_asks_git_case_sensitively():
    """The gate's verdict must be the same on Windows and on Linux CI.

    Before this, a Windows checkout reported five `repo.no-gitignored-source`
    violations that CI could not see: git honoured `core.ignorecase = true`, so
    the lowercase glob `*apikey*` matched `ApiKeysPage.tsx`. The same tree
    failed locally and passed in CI, which is how a gate teaches people to stop
    reading it.
    """
    matched = _check_ignore(
        _CAMEL_CASE_SOURCES + [_LOWERCASE_SOURCE], force_case_sensitive=True
    )

    assert _LOWERCASE_SOURCE in matched, (
        "forcing case sensitivity must not blind the guard — a genuinely "
        "lowercase `api_key` source is still a real finding"
    )
    for path in _CAMEL_CASE_SOURCES:
        assert path not in matched, (
            f"{path} is camelCase; the lowercase glob does not match it on a "
            "case-sensitive filesystem, which is the answer CI gets"
        )


def test_the_guard_passes_the_case_flag_to_git():
    """Pin the mechanism, not just today's answer.

    The behavioural test above passes on Linux even without the flag, because
    there the default already *is* case-sensitive. Only this assertion fails on
    a Linux CI runner if someone removes it, which is exactly where the
    regression would otherwise sail through.
    """
    captured: dict = {}
    real_run = subprocess.run

    def _capture(argv, *args, **kwargs):
        if isinstance(argv, list) and "check-ignore" in argv:
            captured["argv"] = argv
        return real_run(argv, *args, **kwargs)

    original = qg.subprocess.run
    qg.subprocess.run = _capture
    try:
        qg._repo_no_gitignored_source()
    finally:
        qg.subprocess.run = original

    argv = captured.get("argv")
    assert argv, "the guard never invoked git check-ignore"
    joined = " ".join(argv)
    assert "core.ignorecase=false" in joined, (
        "the guard must pin git's case behaviour to CI's; without it the same "
        f"tree gets different verdicts per platform. argv was: {joined}"
    )
    assert "--no-index" in joined, "the --no-index question is the one that matters"


# ── backend.activity-coverage (epic ACT) ─────────────────────────────────────


def _router(tmp_path: Path, name: str, body: str) -> None:
    _write(tmp_path / "backend" / "app" / "routers" / name, body)


def test_activity_coverage_flags_an_unrecorded_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            db.add(Widget())
            await db.commit()
    """)
    violations = qg._backend_activity_coverage()
    assert any("widgets.py" in v.file.as_posix() for v in violations)


def test_activity_coverage_accepts_a_handler_that_records_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        from app.services.activity.service import record as record_activity

        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db, current_user):
            db.add(Widget())
            await record_activity(db, project_id=project_id, event_type="policy.updated")
            await db.commit()
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_follows_a_dotted_service_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`from app.services.foo_service import bar` — the router delegates and
    the SERVICE records. Flagging the router here would be a false positive."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "widget_service.py", """
        from app.services.activity.service import record
        async def do_it(db):
            await record(db, event_type="policy.updated")
    """)
    _router(tmp_path, "widgets.py", """
        from app.services.widget_service import do_it

        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            await do_it(db)
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_follows_a_grouped_service_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`from app.services import foo_service as svc` — the OTHER import form.

    Missing it reported flaky_quarantine.py as uncovered while its service
    records on its behalf. A false entry in the baseline is worse than no
    guard: it teaches the reader to ignore the list.
    """
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "widget_service.py", """
        from app.services.activity.service import record
        async def do_it(db):
            await record(db, event_type="policy.updated")
    """)
    _router(tmp_path, "widgets.py", """
        from app.services import widget_service as svc

        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            await svc.do_it(db)
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_honours_an_explicit_opt_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        # activity: none - preview only, mutates nothing
        @router.post("/{project_id}/widgets/preview")
        async def preview_widget(project_id, db):
            return {"preview": True}
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_skips_routers_with_no_project_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """auth/sso/settings mutate instance-wide state. They are audited by
    settings_audit_log and identity_events, which have no project column
    precisely because those events have no project."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "auth.py", """
        @router.post("/login")
        async def login(db):
            return {"token": "x"}
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_ignores_read_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        @router.get("/{project_id}/widgets")
        async def list_widgets(project_id, db):
            return []
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_is_per_endpoint_not_per_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One recording handler must not cover its unrecorded siblings.

    The guard used to ask "does this router record anywhere?". Adding the
    first event to routers/agents.py (E7.4 retry/cancel) answered yes for the
    whole file, and four tracked gaps in the same router went stale with
    nothing left tracking them.
    """
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        from app.services.activity.service import record as record_activity

        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            await record_activity(db, project_id=project_id, event_type="policy.updated")

        @router.delete("/{project_id}/widgets/{widget_id}")
        async def delete_widget(project_id, widget_id, db):
            await db.delete(widget_id)
    """)
    violations = qg._backend_activity_coverage()
    assert [v.line for v in violations] == [7], [v.format() for v in violations]


def test_activity_coverage_accepts_a_handler_that_calls_a_recording_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A module-level helper that records covers the handlers that call it."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        from app.services.activity.service import record as record_activity

        async def _emit(db, project_id):
            await record_activity(db, project_id=project_id, event_type="policy.updated")

        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            await _emit(db, project_id)
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_flags_a_handler_that_never_calls_the_recording_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Importing a recording service covers only the handlers that call it."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "widget_service.py", """
        from app.services.activity.service import record
        async def approve(db):
            await record(db, event_type="policy.updated")
    """)
    _router(tmp_path, "widgets.py", """
        from app.services import widget_service as svc

        @router.post("/{project_id}/widgets/approve")
        async def approve_widget(project_id, db):
            await svc.approve(db)

        @router.post("/{project_id}/widgets/archive")
        async def archive_widget(project_id, db):
            await db.commit()
    """)
    violations = qg._backend_activity_coverage()
    assert [v.line for v in violations] == [7], [v.format() for v in violations]


def test_activity_coverage_follows_an_import_made_inside_the_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Routers import services lazily inside the handler to dodge cycles."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "widget_service.py", """
        from app.services.activity.service import record
        async def do_it(db):
            await record(db, event_type="policy.updated")
    """)
    _router(tmp_path, "widgets.py", """
        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            from app.services.widget_service import do_it
            await do_it(db)
    """)
    assert qg._backend_activity_coverage() == []


def test_activity_coverage_reports_every_uncovered_mutation_not_just_the_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A guard that stops at the first hit understates the gap, and the
    baseline it generates would then let the rest through silently."""
    _redirect_repo_root(monkeypatch, tmp_path)
    _router(tmp_path, "widgets.py", """
        @router.post("/{project_id}/widgets")
        async def create_widget(project_id, db):
            pass

        @router.delete("/{project_id}/widgets/{widget_id}")
        async def delete_widget(project_id, widget_id, db):
            pass
    """)
    assert len(qg._backend_activity_coverage()) == 2


# ── ci.every-test-suite-runs (re-audit N2) ───────────────────────────────────
#
# The MCP suite was collected by nothing while its auth gate became the
# security boundary. These pin the parts that decide "is this suite run":
# what a suite is, what a runner command is, and whether a command covers it.

_WORKFLOW = """\
name: t
jobs:
  py:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: ./mcp
    steps:
      - uses: actions/checkout@v7
      - name: deps
        run: pip install pytest
      - name: tests
        run: python -m pytest tests -q
  sdk:
    runs-on: ubuntu-latest
    steps:
      - name: sdk + cli
        run: |
          pip install ./client
          python -m pytest client/tests -q
      - name: scripts
        run: |
          cd scripts
          python -m pytest test_gate.py -v
      - name: go
        working-directory: client/go
        run: go test ./...
      - name: folded
        working-directory: ./backend
        run: >-
          pytest -m integration -v
          tests/integration/test_a.py
"""


def test_suites_are_topmost_test_dirs_and_skip_product_code_named_test() -> None:
    units = qg._test_units([
        "mcp/tests/test_a.py",
        "mcp/tests/sub/test_b.py",
        "backend/app/routers/test_runs.py",      # product code, not a test
        "backend/test_db.py",                    # a manual script
        "scripts/test_gate.py",
        "client/go/pkg/x_test.go",
        "frontend/src/a/B.test.tsx",
        "client/java/src/test/java/io/ATest.java",
        "mcp/tests/conftest.py",
    ])
    assert units == {
        "mcp/tests", "scripts/test_gate.py", "client/go/pkg/x_test.go",
        "frontend/src/a/B.test.tsx", "client/java/src/test",
    }


def test_workflow_commands_resolve_the_effective_directory() -> None:
    commands = qg._workflow_test_commands(_WORKFLOW)
    assert commands == [
        ("mcp", "python -m pytest tests -q"),
        ("", "python -m pytest client/tests -q"),
        ("scripts", "python -m pytest test_gate.py -v"),
        ("client/go", "go test ./..."),
        ("backend", "pytest -m integration -v tests/integration/test_a.py"),
    ]


def test_installing_pytest_is_not_running_anything() -> None:
    commands = qg._workflow_test_commands(_WORKFLOW)
    # `pip install pytest` has no path argument; read as a runner it would
    # count as "run every suite under the repo root" and pass everything.
    assert all(not c.startswith("pip ") for _, c in commands)
    assert not qg._unit_is_run("client/js/tests", commands)


def test_a_suite_is_run_only_by_a_command_that_names_or_contains_it() -> None:
    commands = qg._workflow_test_commands(_WORKFLOW)
    assert qg._unit_is_run("mcp/tests", commands)
    assert qg._unit_is_run("client/tests", commands)
    assert qg._unit_is_run("scripts/test_gate.py", commands)
    assert qg._unit_is_run("client/go/pkg/x_test.go", commands)
    # Running ONE file of a suite does not run the suite.
    assert not qg._unit_is_run("backend/tests", commands)


def test_the_real_repo_runs_every_suite_and_the_mcp_step_is_what_runs_it() -> None:
    assert qg._ci_every_test_suite_runs() == []
    ci = (qg.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = "        run: python -m pytest tests -q\n"
    assert ci.count(step) == 1
    commands = qg._workflow_test_commands(ci.replace(step, "        run: echo skipped\n"))
    assert not qg._unit_is_run("mcp/tests", commands), (
        "removing the MCP pytest step must leave mcp/tests unrun"
    )


def test_a_bare_directory_argument_is_a_path_not_the_whole_package() -> None:
    # `pytest tests` from the root runs root/tests, not every suite in the repo.
    assert not qg._unit_is_run("mcp/other_tests", [("", "pytest tests")])
    assert qg._unit_is_run("tests", [("", "pytest tests")])
    # `-m integration` is an option value, not a path.
    assert qg._command_paths("pytest -m integration -v tests/a.py") == (["tests/a.py"], False)


def test_a_config_file_narrows_the_run() -> None:
    commands = [("frontend", "npx playwright test --config playwright.docs.config.ts")]
    assert not qg._unit_is_run("frontend/tests", commands)
    assert qg._unit_is_run("frontend/src/a.test.ts", [("frontend", "npm run test")])


def test_a_parser_that_finds_no_commands_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    # "No test commands found" must not read as "every suite is covered".
    monkeypatch.setattr(qg, "_workflow_test_commands", lambda text: [])
    violations = qg._ci_every_test_suite_runs()
    assert len(violations) == 1
    assert "cannot have looked properly" in violations[0].message


# QA-B45-P3: a suite counts as RUN only if its failure can fail the build.
_GATING = """\
name: t
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: suite
        run: python -m pytest a/tests -q
  b:
    runs-on: ubuntu-latest
    steps:
      - name: other
        run: python -m pytest b/tests -q
"""
_STEP = "      - name: suite\n"
_JOB = "  a:\n    runs-on: ubuntu-latest\n"
_CMD = "        run: python -m pytest a/tests -q\n"


def _a_runs(text: str) -> bool:
    commands = qg._workflow_test_commands(text)
    assert qg._unit_is_run("b/tests", commands), "control: job b must stay run"
    return qg._unit_is_run("a/tests", commands)


def _variant(anchor: str, replacement: str) -> str:
    assert _GATING.count(anchor) == 1, anchor
    return _GATING.replace(anchor, replacement)


def test_the_gating_fixture_runs_the_suite() -> None:
    assert _a_runs(_GATING)


@pytest.mark.parametrize("mutated", [
    _variant(_STEP, _STEP + "        if: false\n"),
    _variant(_STEP, _STEP + "        if: ${{ false }}\n"),
    _variant(_STEP, _STEP + "        if: \"false\"   # parked\n"),
    _variant(_STEP, _STEP + "        if: false && github.event_name == 'push'\n"),
    _variant(_STEP, "      - if: false\n        name: suite\n"),
    _variant(_JOB, _JOB + "    if: false\n"),
    _variant(_JOB, _JOB + "    if: ${{ false }}\n"),
    _variant(_CMD, _CMD + "    if: false\n"),                  # job key AFTER steps
    _variant(_STEP, _STEP + "        continue-on-error: true\n"),
    _variant(_STEP, _STEP + "        continue-on-error: ${{ true }}\n"),
    _variant(_JOB, _JOB + "    continue-on-error: true\n"),
], ids=[
    "step-if-false", "step-if-expr-false", "step-if-quoted-false", "step-if-false-and",
    "step-if-first-key", "job-if-false", "job-if-expr-false", "job-if-after-steps",
    "step-continue-on-error", "step-continue-on-error-expr", "job-continue-on-error",
])
def test_a_step_or_job_that_cannot_fail_the_build_runs_nothing(mutated: str) -> None:
    assert not _a_runs(mutated)


@pytest.mark.parametrize("command", [
    "python -m pytest a/tests -q || true",
    "python -m pytest a/tests -q || :",
    "python -m pytest a/tests -q; true",
    "python -m pytest a/tests -q ; :",
    "python -m pytest a/tests -q || echo tests failed",
    "python -m pytest a/tests -q || exit 0",
])
def test_a_swallowed_exit_status_runs_nothing(command: str) -> None:
    assert not _a_runs(_variant(_CMD, f"        run: {command}\n"))


@pytest.mark.parametrize("script", [
    "set +e\n          python -m pytest a/tests -q",
    "set +o errexit\n          python -m pytest a/tests -q",
    "set -x +e\n          python -m pytest a/tests -q",
])
def test_set_plus_e_before_the_runner_runs_nothing(script: str) -> None:
    # Something runs after the runner, so its failure is dropped.
    assert not _a_runs(_variant(_CMD, f"        run: |\n          {script}\n          echo done\n"))
    # As the LAST command its status is the step's, errexit or not (QA-B45-R3-2).
    assert _a_runs(_variant(_CMD, f"        run: |\n          {script}\n"))


_BARE = "        run: python -m pytest a/tests -q\n"
_THEN_MORE = "        run: |\n          python -m pytest a/tests -q\n          echo done\n"
_PIPED = "        run: python -m pytest a/tests -q | tee log\n"


@pytest.mark.parametrize("shell,bare,then_more,piped", [
    ("bash -l {0}", True, False, False),
    ("bash -e {0}", True, True, False),
    ("bash -eo pipefail {0}", True, True, True),
    ("bash --noprofile --norc -eo pipefail {0}", True, True, True),
    ("/bin/bash -l {0}", True, False, False),
    ("bash -l -o errexit {0}", True, True, False),
    ("bash", True, True, True),
    ("sh", True, True, False),
    ("pwsh", True, False, False),
    ("powershell", True, False, False),
], ids=["bash-l", "bash-e", "bash-eo-pipefail", "bash-full", "abs-bash-l", "bash-o-errexit",
        "bash", "sh", "pwsh", "powershell"])
def test_each_shell_is_judged_by_what_it_runs(shell: str, bare: bool, then_more: bool, piped: bool) -> None:
    """QA-B45-R3-2: `shell: bash -l {0}` (no -e, no pipefail) with pytest as
    the last line gates -- the old gate counted it, round 2 did not. A runner
    followed by more commands without errexit, or piped without pipefail,
    does not."""
    step = f"        shell: {shell}\n"
    assert _a_runs(_variant(_CMD, step + _BARE)) is bare
    assert _a_runs(_variant(_CMD, step + _THEN_MORE)) is then_more
    assert _a_runs(_variant(_CMD, step + _PIPED)) is piped


@pytest.mark.parametrize("mutated", [
    _variant(_STEP, _STEP + "        if: github.event_name == 'push'\n"),
    _variant(_STEP, _STEP + "        if: always()\n"),
    _variant(_STEP, _STEP + "        if: false || github.event_name == 'push'\n"),
    _variant(_STEP, _STEP + "        if: ${{ 'false' }}\n"),      # a non-empty string: truthy
    _variant(_STEP, _STEP + "        continue-on-error: false\n"),
    _variant(_JOB, _JOB + "    if: github.ref == 'refs/heads/main'\n"),
    _variant(_CMD, "        run: python -m pytest a/tests -q || exit 1\n"),
    _variant(_CMD, "        run: python -m pytest a/tests -q || (echo failed; exit $?)\n"),
    _variant(_CMD, "        run: python -m pytest a/tests -q || python -m pytest a/tests --lf\n"),
    _variant(_CMD, "        run: |\n          set +e\n          x=1\n          set -e\n"
                   "          python -m pytest a/tests -q\n"),
    _variant(_CMD, _CMD + "      - name: later\n        if: false\n        run: echo x\n"),
], ids=[
    "if-event", "if-always", "if-false-or", "if-string-false", "coe-false",
    "job-if-branch", "or-exit-1", "or-exit-status", "or-retry", "set-e-restored",
    "a-later-disabled-step",
])
def test_conditional_or_failure_preserving_steps_still_run(mutated: str) -> None:
    assert _a_runs(mutated)


def test_a_disabled_job_does_not_leak_into_the_next_job() -> None:
    text = _variant("  b:\n", "  b:\n    if: false\n")
    commands = qg._workflow_test_commands(text)
    assert qg._unit_is_run("a/tests", commands)
    assert not qg._unit_is_run("b/tests", commands)


@pytest.mark.parametrize("anchor,replacement", [
    ("      - name: Self-test the drift guard\n",
     "      - name: Self-test the drift guard\n        if: false\n"),
    ("  image-manifest-drift:\n", "  image-manifest-drift:\n    if: false\n"),
    ("scripts/release/tests -v\n", "scripts/release/tests -v || true\n"),
    ("      - name: Self-test the drift guard\n",
     "      - name: Self-test the drift guard\n        continue-on-error: true\n"),
], ids=["step-if-false", "job-if-false", "or-true", "continue-on-error"])
def test_the_real_release_suite_is_unrun_when_its_step_cannot_fail(
    anchor: str, replacement: str,
) -> None:
    ci = (qg.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count(anchor) == 1, anchor
    assert qg._unit_is_run("scripts/release/tests", qg._workflow_test_commands(ci))
    commands = qg._workflow_test_commands(ci.replace(anchor, replacement))
    assert not qg._unit_is_run("scripts/release/tests", commands)


# ── R-B45-R2-6 / QA-B45-R2-2: more forms whose failure cannot fail the build ──
#
# Statically decidable forms are decided; an undecidable `continue-on-error:`
# fails safe (NOT run). What stays out of reach -- an `if:` over env/matrix/
# step outputs, a runner hidden in a script -- is undecidable here and is
# counted as run.


@pytest.mark.parametrize("mutated", [
    _variant(_STEP, _STEP + "        if: false || false\n"),
    _variant(_STEP, _STEP + "        if: ${{ !true }}\n"),
    _variant(_STEP, _STEP + "        if: 1 == 2\n"),
    _variant(_STEP, _STEP + "        if: ${{ !(true && true) }}\n"),
    _variant(_STEP, _STEP + "        if: null\n"),
    _variant(_STEP, _STEP + "        if: ${{ 'a' == 'b' }}\n"),
    _variant(_JOB, _JOB + "    if: false || (1 == 2)\n"),
    _variant(_STEP, _STEP + "        continue-on-error: ${{ env.SOFT }}\n"),
    _variant(_STEP, _STEP + "        continue-on-error: ${{ matrix.experimental }}\n"),
    _variant(_JOB, _JOB + "    continue-on-error: ${{ fromJSON(env.SOFT) }}\n"),
], ids=[
    "if-false-or-false", "if-not-true", "if-1-eq-2", "if-not-and", "if-null", "if-string-neq",
    "job-if-false-or-cmp", "coe-env", "coe-matrix", "job-coe-function",
])
def test_a_constant_false_if_or_an_undecidable_soft_fail_runs_nothing(mutated: str) -> None:
    assert not _a_runs(mutated)


def test_the_expression_reader_is_three_valued() -> None:
    assert qg._constant_truth("false || false") is False
    assert qg._constant_truth("!true") is False
    assert qg._constant_truth("!(1 == 2)") is True
    assert qg._constant_truth("'false'") is True            # a non-empty string
    assert qg._constant_truth("false || always()") is None
    assert qg._constant_truth("true && env.x") is None
    assert qg._constant_truth("false && env.x") is False
    assert qg._constant_truth("contains(github.ref, 'x') || false") is None
    assert qg._constant_truth("github['ref']") is None       # unsupported syntax: undecided
    assert qg._constant_truth("github.event_name == 'schedule'", {"push"}) is False
    assert qg._constant_truth("github.event_name == 'push'", {"push"}) is None
    assert qg._constant_truth("github.event_name != 'schedule'", {"push"}) is True
    assert qg._constant_truth("github.event_name == 'schedule'", None) is None


def test_an_event_the_workflow_is_never_triggered_by_runs_nothing() -> None:
    on = _variant("name: t\n", "name: t\non: [push, pull_request]\n")
    assert _a_runs(on)
    for event, runs in (("schedule", False), ("push", True)):
        text = on.replace(_STEP, _STEP + f"        if: github.event_name == '{event}'\n")
        assert on.count(_STEP) == 1 and _a_runs(text) is runs, event
    # With no `on:` to read, an event comparison stays undecided: counted as run.
    assert _a_runs(_variant(_STEP, _STEP + "        if: github.event_name == 'schedule'\n"))


_UPSTREAM = (
    "  c:\n    if: false\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - name: c\n        run: echo c\n"
)


@pytest.mark.parametrize("needs", [
    "    needs: c\n", "    needs: [c]\n", "    needs: [b, c]\n", "    needs:\n      - b\n      - c\n",
], ids=["scalar", "flow", "flow-two", "block"])
def test_a_job_that_needs_a_disabled_job_runs_nothing(needs: str) -> None:
    assert not _a_runs(_variant(_JOB, _JOB + needs) + _UPSTREAM)


def test_a_skipped_upstream_propagates_and_always_opts_out() -> None:
    chain = (
        _variant(_JOB, _JOB + "    needs: d\n") + _UPSTREAM
        + "  d:\n    needs: c\n    runs-on: ubuntu-latest\n    steps:\n      - name: d\n        run: echo d\n"
    )
    assert not _a_runs(chain)
    assert _a_runs(_variant(_JOB, _JOB + "    needs: c\n    if: always()\n") + _UPSTREAM)
    assert _a_runs(_variant(_JOB, _JOB + "    needs: b\n"))          # an enabled upstream


_PIPE = "        run: python -m pytest a/tests -q | tee log\n"


@pytest.mark.parametrize("run", [
    _PIPE,
    "        run: python -m pytest a/tests -q |& tee log\n",
    "        run: python -m pytest a/tests -q 2>&1 | tee log\n",
    "        run: |\n          python -m pytest a/tests -q && echo ok\n          echo more\n",
    "        run: python -m pytest a/tests -q && echo ok; echo done\n",
    "        run: |\n          set -o pipefail\n          set +o pipefail\n          python -m pytest a/tests -q | tee log\n",
    "        shell: sh\n" + _PIPE,
    "        shell: bash -e {0}\n" + _PIPE,
    "        run: python -m pytest a/tests -q --collect-only\n",
    "        run: python -m pytest a/tests --co -q\n",
], ids=[
    "pipe", "pipe-stderr", "redirect-then-pipe", "and-list-then-more", "and-list-then-semicolon",
    "pipefail-turned-off", "shell-sh-pipe", "custom-shell-no-pipefail", "collect-only", "co",
])
def test_a_runner_whose_status_a_pipe_or_list_drops_runs_nothing(run: str) -> None:
    assert not _a_runs(_variant(_CMD, run))


@pytest.mark.parametrize("run", [
    "        run: |\n          set -euo pipefail\n          python -m pytest a/tests -q | tee log\n",
    "        run: |\n          set -e -o pipefail\n          python -m pytest a/tests -q | tee log\n",
    "        shell: bash\n" + _PIPE,
    "        shell: bash -eo pipefail {0}\n" + _PIPE,
    "        run: python -m pytest a/tests -q && echo ok\n",
    "        run: |\n          echo start\n          python -m pytest a/tests -q && echo ok\n",
    "        run: |\n          python -m pytest a/tests -q && echo ok || exit 1\n          echo more\n",
    "        run: python -m pytest -k 'a|b' a/tests -q\n",
    "        run: python -m pytest a/tests -q 2>&1\n",
    "        run: |\n          python -m pytest a/tests -q\n          echo ok | tee x\n",
    "        run: python -m pytest a/tests -q --cov=app --color=yes\n",
], ids=[
    "set-euo-pipefail", "set-e-o-pipefail", "shell-bash", "custom-shell-pipefail", "and-list-last",
    "and-list-last-after-others", "and-then-or-exit", "quoted-pipe", "redirect-only",
    "pipe-on-another-line", "cov-is-not-co",
])
def test_a_runner_whose_status_still_fails_the_step_runs(run: str) -> None:
    assert _a_runs(_variant(_CMD, run))


def test_a_job_or_workflow_default_shell_bash_brings_pipefail() -> None:
    piped = _variant(_CMD, _PIPE)
    assert not _a_runs(piped)  # control: the default shell has no pipefail
    job = piped.replace(_JOB, _JOB + "    defaults:\n      run:\n        shell: bash\n")
    assert piped.count(_JOB) == 1 and _a_runs(job)
    workflow = piped.replace("jobs:\n", "defaults:\n  run:\n    shell: bash\njobs:\n")
    assert piped.count("jobs:\n") == 1 and _a_runs(workflow)


@pytest.mark.parametrize("on,gates", [
    ("on: [push, pull_request]\n", True),
    ("on: push\n", True),
    ("on:\n  pull_request:\n    branches: [main]\n  workflow_dispatch:\n", True),
    ("on:\n  workflow_call:\n    inputs: {}\n", True),
    ("on:\n  workflow_dispatch:\n    inputs:\n      x: {type: string}\n", False),
    ("on: [workflow_dispatch, schedule]\n", False),
    ("on:\n  - schedule\n", False),
    ("", False),  # no readable trigger: loud, not a pass
], ids=["flow", "scalar", "block", "call", "dispatch-only", "flow-no-gating", "block-list", "none"])
def test_only_a_workflow_a_push_or_pull_request_triggers_gates(on: str, gates: bool) -> None:
    assert qg._workflow_gates("name: t\n" + on + "jobs:\n  a:\n    runs-on: x\n") is gates


def test_the_real_ci_moved_to_dispatch_only_runs_no_suite() -> None:
    ci = (qg.REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    triggers = "  push:\n    branches: [main, develop]\n  pull_request:\n    branches: [main]\n"
    assert ci.count(triggers) == 1
    assert qg._unit_is_run("mcp/tests", qg._gating_workflow_commands(ci))
    assert qg._gating_workflow_commands(ci.replace(triggers, "")) == []


# ── backend.status-enum-vocab, generalised (re-audit L3) ────────────────────
#
# The guard used to recognise only columns literally named `status`. Any
# column whose Mapped[...] annotation or default names an enum carries the
# same trap: `User.role == "admin"` against stored "ADMIN" matches nothing.

_ENUM_MODELS = """
    from enum import Enum
    class UserRole(str, Enum):
        ADMIN = "ADMIN"
        VIEWER = "VIEWER"
    class Channel(Enum):
        EMAIL = "email"
        SLACK = "slack"
    class Status(str, Enum):
        OPEN = "OPEN"
        CLOSED = "CLOSED"
    class User(Base):
        role: Mapped[UserRole] = mapped_column(String(20), default=UserRole.VIEWER.value)
        name: Mapped[str] = mapped_column(String(20), default="x")
    class Org(Base):
        default_role: Mapped[UserRole] = mapped_column(String(20))
    class Digest(Base):
        channel: Mapped[str] = mapped_column(String(20), default=Channel.EMAIL.value)
        status: Mapped[str] = mapped_column(String(20), default=Status.OPEN.value)
"""


def _enum_repo(tmp_path: Path, service: str) -> None:
    _write(tmp_path / "backend" / "app" / "models" / "postgres.py", _ENUM_MODELS)
    _write(tmp_path / "backend" / "app" / "services" / "svc.py", service)


def test_enum_backed_columns_are_found_by_annotation_or_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _enum_repo(tmp_path, "x = 1\n")
    columns, enums = qg._enum_column_vocabularies()
    assert columns == {
        ("User", "role"): "UserRole",
        ("Org", "default_role"): "UserRole",      # annotation only, no default
        ("Digest", "channel"): "Channel",          # default only, Mapped[str]
        ("Digest", "status"): "Status",
    }
    assert enums["Channel"] == {"email", "slack"}


def test_every_enum_backed_column_is_checked_not_only_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _enum_repo(tmp_path, """
        def q():
            select(User).where(User.role == "admin")
            select(User).where(User.role == "ADMIN")
            select(Org).where(Org.default_role != "Viewer")
            select(Digest).where(Digest.channel.in_(["email", "sms"]))
            select(Digest).where(Digest.channel.notin_(("pager",)))
            select(Digest).where(Digest.status != "closed")
            select(User).where(User.name == "anything at all")
    """)
    messages = sorted(v.message for v in qg._backend_status_enum_vocab())
    assert len(messages) == 5, messages
    assert any("User.role compared to 'admin'" in m and "did you mean 'ADMIN'" in m for m in messages)
    assert any("Org.default_role compared to 'Viewer'" in m for m in messages)
    assert any("Digest.channel compared to 'sms'" in m for m in messages)
    assert any("Digest.channel compared to 'pager'" in m for m in messages)
    assert any("Digest.status compared to 'closed'" in m and "did you mean 'CLOSED'" in m for m in messages)
    assert not any("User.name" in m for m in messages)


def test_enum_vocab_fails_loud_when_no_column_is_enum_backed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "models" / "postgres.py", """
        class User(Base):
            name: Mapped[str] = mapped_column(String(20))
    """)
    violations = qg._backend_status_enum_vocab()
    assert len(violations) == 1
    assert "checked nothing" in violations[0].message


def test_enum_vocab_real_models_cover_more_than_status() -> None:
    columns, _ = qg._enum_column_vocabularies()
    assert {c for (_, c) in columns} - {"status"}, "only status columns found: the L3 widening regressed"


# ── ci.dependabot-covers-every-manifest (re-audit M23) ──────────────────────


def test_dependabot_entries_parse_quoted_unquoted_and_commented() -> None:
    text = (
        'version: 2\nupdates:\n'
        '  - package-ecosystem: "pip"\n    directory: "/backend"\n'
        '  - package-ecosystem: npm\n    directory: /client/js/   # the JS SDK\n'
        '  - package-ecosystem: "github-actions"\n    directory: "/"\n'
    )
    assert qg._dependabot_entries(text) == {
        ("pip", "/backend"), ("npm", "/client/js"), ("github-actions", "/"),
    }


def test_dependabot_gaps_name_each_uncovered_manifest() -> None:
    tracked = [
        "backend/requirements.txt", "mcp/requirements.txt", "mcp/Dockerfile",
        "client/js/package.json", "client/examples/python/x/requirements.txt",
        "frontend/node_modules/a/package.json", ".github/workflows/ci.yml", "README.md",
    ]
    text = 'updates:\n  - package-ecosystem: "pip"\n    directory: "/backend"\n'
    assert qg._dependabot_gaps(tracked, text) == [
        ("client/js/package.json", "npm", "/client/js"),
        ("mcp/Dockerfile", "docker", "/mcp"),
        ("mcp/requirements.txt", "pip", "/mcp"),
        (".github/workflows", "github-actions", "/"),
    ]


def test_the_real_dependabot_config_covers_every_manifest() -> None:
    import subprocess

    assert qg._ci_dependabot_covers_every_manifest() == []
    text = (qg.REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    entry = '  - package-ecosystem: "pip"\n    directory: "/mcp"\n'
    assert text.count(entry) == 1
    tracked = subprocess.run(["git", "ls-files"], cwd=qg.REPO_ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    gaps = qg._dependabot_gaps(tracked, text.replace(entry, ""))
    assert [gap[0] for gap in gaps] == ["mcp/requirements.txt"]


# QA-B45-P4: the plural `directories:` form, its globs, and the manifest kinds
# the guard used to skip.


def test_dependabot_directories_block_and_flow_forms() -> None:
    text = (
        'updates:\n'
        '  - package-ecosystem: "npm"\n'
        '    directories:\n'
        '      - "/frontend"\n'
        '      # a comment inside the list\n'
        "      - '/client/js/'   # the JS SDK\n"
        '    schedule:\n      interval: "weekly"\n'
        '  - package-ecosystem: pip\n'
        '    directories:\n'
        '    - /mcp\n'                               # items at the key's indent
        '    - "/libs/*"\n'
        '  - package-ecosystem: docker\n'
        '    directories: ["/backend", "/mcp"]  # flow form\n'
        '  - package-ecosystem: "github-actions"\n    directory: "/"\n'
    )
    assert qg._dependabot_entries(text) == {
        ("npm", "/frontend"), ("npm", "/client/js"),
        ("pip", "/mcp"), ("pip", "/libs/*"),
        ("docker", "/backend"), ("docker", "/mcp"),
        ("github-actions", "/"),
    }


def test_a_directories_list_ends_at_the_next_key() -> None:
    text = (
        'updates:\n'
        '  - package-ecosystem: "npm"\n'
        '    directories:\n'
        '      - "/frontend"\n'
        '    ignore:\n'
        '      - "/not-a-directory"\n'
    )
    assert qg._dependabot_entries(text) == {("npm", "/frontend")}


def test_dependabot_directory_globs_cover_by_segment() -> None:
    entries = {("pip", "/libs/*"), ("npm", "/**"), ("docker", "/svc-?")}
    assert qg._dependabot_covers(entries, "pip", "/libs/a")
    assert not qg._dependabot_covers(entries, "pip", "/libs/a/b")    # `*` is one segment
    assert not qg._dependabot_covers(entries, "pip", "/libs")
    assert qg._dependabot_covers(entries, "npm", "/libs/a")
    assert qg._dependabot_covers(entries, "npm", "/")
    assert qg._dependabot_covers(entries, "npm", "/a/b/c")
    assert qg._dependabot_covers(entries, "docker", "/svc-1")
    assert not qg._dependabot_covers(entries, "docker", "/svc-12")
    assert not qg._dependabot_covers(entries, "pip", "/other")       # wrong ecosystem/dir
    assert not qg._dependabot_covers({("pip", "/libs")}, "pip", "/libs/a")
    # `/libs/**` is the directory itself and everything below it, not a prefix.
    deep = {("gradle", "/libs/**")}
    assert qg._dependabot_covers(deep, "gradle", "/libs")
    assert qg._dependabot_covers(deep, "gradle", "/libs/a/b")
    assert not qg._dependabot_covers(deep, "gradle", "/libsx")


def test_the_real_config_in_directories_form_still_covers_everything() -> None:
    import subprocess

    text = (qg.REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    entry = '  - package-ecosystem: "pip"\n    directory: "/mcp"\n'
    assert text.count(entry) == 1
    plural = text.replace(entry, '  - package-ecosystem: "pip"\n    directories:\n      - "/mcp"\n')
    tracked = subprocess.run(["git", "ls-files"], cwd=qg.REPO_ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    assert qg._dependabot_gaps(tracked, plural) == []


@pytest.mark.parametrize("manifest,ecosystem", [
    ("tools/rs/Cargo.toml", "cargo"),
    ("tools/gr/build.gradle", "gradle"),
    ("tools/kt/build.gradle.kts", "gradle"),
    ("tools/legacy/setup.py", "pip"),
    ("tools/pe/Pipfile", "pip"),
])
def test_every_manifest_kind_dependabot_updates_is_enumerated(manifest: str, ecosystem: str) -> None:
    text = 'updates:\n  - package-ecosystem: "github-actions"\n    directory: "/"\n'
    directory = "/" + manifest.rpartition("/")[0]
    assert qg._dependabot_gaps([manifest], text) == [(manifest, ecosystem, directory)]
    covered = text + f'  - package-ecosystem: "{ecosystem}"\n    directories: ["{directory}"]\n'
    assert qg._dependabot_gaps([manifest], covered) == []


# ── Guard: agents.pipeline-status-writes-via-state-machine (E7.1) ─────────────


def test_pipeline_status_direct_assignment_is_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "agents" / "leaky.py", """
        from app.models.postgres import AgentPipelineRun
        async def finish(db, pipeline_id):
            pipeline = (await db.execute(select(AgentPipelineRun))).scalar_one()
            pipeline.status = "failed"
    """)
    violations = qg._agents_pipeline_status_writes_via_state_machine()
    assert len(violations) == 1
    assert "pipeline.status assigned directly" in violations[0].message


def test_pipeline_status_write_inside_state_machine_is_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "workflow_run_state.py", """
        from app.models.postgres import AgentPipelineRun
        def apply_transition(pipeline, to):
            pipeline.status = to
    """)
    assert qg._agents_pipeline_status_writes_via_state_machine() == []


def test_unrelated_status_assignment_is_not_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "outbox.py", """
        from app.models.postgres import RunDownstreamOutbox
        def fail(row):
            row.status = "failed"
    """)
    assert qg._agents_pipeline_status_writes_via_state_machine() == []


def test_apply_transition_call_is_not_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "agents" / "ok.py", """
        from app.models.postgres import AgentPipelineRun
        from app.services.workflow_run_state import apply_transition
        async def finish(db):
            pipeline = (await db.execute(select(AgentPipelineRun))).scalar_one()
            apply_transition(pipeline, "failed", error="x")
            pipeline.completed_at = None
    """)
    assert qg._agents_pipeline_status_writes_via_state_machine() == []


# ── Guard: agents.no-partial-pipeline-status (E7.1) ───────────────────────────


@pytest.mark.parametrize("snippet", [
    'q = select(AgentPipelineRun).where(AgentPipelineRun.status == "partial")',
    'q = select(AgentPipelineRun).where(AgentPipelineRun.status.in_(["failed", "partial"]))',
    'q = select(AgentPipelineRun).where(AgentPipelineRun.status.in_({"completed", "cancelled"}))',
    'TERMINAL = ("completed", "partial")\nq = select(AgentPipelineRun).where(AgentPipelineRun.status.in_(TERMINAL))',
])
def test_partial_pipeline_status_literal_is_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, snippet: str) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "stale.py", "from sqlalchemy import select\n" + snippet + "\n")
    violations = qg._agents_no_partial_pipeline_status()
    assert len(violations) == 1, [v.message for v in violations]


def test_partial_on_other_models_is_not_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "other.py", """
        from sqlalchemy import select
        q = select(DeletionJob).where(DeletionJob.status == "partial")
        r = select(AgentPipelineRun).where(AgentPipelineRun.status.in_(["completed", "passed"]))
    """)
    assert qg._agents_no_partial_pipeline_status() == []


# -- reviews.* guards (architecture E8.6) ---------------------------------------

_REVIEW_PLANNER = (
    '_OFFLINE_STAGES = ("summary", "triage")\n'
    '_LIVE_STAGES = ("summary",)\n'
    '_INVESTIGATION_STAGES = ("investigator_plan",)\n'
    '_DEEP_STAGES = ("summary",)\n'
)


def _review_producer_repo(
    tmp_path: Path,
    *,
    extra: str = "",
    schemas: str = '"PreliminarySummary", "AnalysisAgentOutput", "DecisionReportV1", "RefinedReport"',
    finalize: bool = True,
) -> None:
    _write(
        tmp_path / "backend" / "app" / "services" / "agent_capability_registry.py",
        f"REPORT_OUTPUT_SCHEMAS: frozenset[str] = frozenset({{{schemas}}})\n"
        "_SPECS = (\n"
        '    _capability("summary", inputs="X", output="PreliminarySummary"),\n'
        '    _capability("triage", inputs="X", output="TriageOutput"),\n'
        f"{extra}"
        ")\n",
    )
    _write(tmp_path / "backend" / "app" / "services" / "agent_planner.py", _REVIEW_PLANNER)
    _write(
        tmp_path / "backend" / "app" / "agents" / "workflow.py",
        (
            "produced = review_request_service.report_stages(stages)\n"
            "await review_request_service.stage_run_review_request(db, run=run)\n"
        ) if finalize else "produced = []\n",
    )
    _write(
        tmp_path / "backend" / "app" / "services" / "review_request_service.py",
        "def report_stages(rows):\n    return [r for r in rows if is_report_producing(r)]\n",
    )


def test_report_producers_guard_passes_a_planned_report_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _review_producer_repo(tmp_path)
    assert qg._reviews_report_producers_create_review_request() == []


def test_report_producers_guard_flags_an_on_demand_report_capability(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _review_producer_repo(
        tmp_path,
        extra='    _capability("decision_report", execution="on_demand", inputs="X", output="DecisionReportV1"),\n',
    )
    messages = [v.message for v in qg._reviews_report_producers_create_review_request()]
    assert len(messages) == 1 and "'decision_report'" in messages[0] and "on_demand" in messages[0]


def test_report_producers_guard_flags_a_report_stage_no_workflow_plans(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _review_producer_repo(tmp_path, extra='    _capability("report_refinement", inputs="X", output="RefinedReport"),\n')
    messages = [v.message for v in qg._reviews_report_producers_create_review_request()]
    assert len(messages) == 1 and "'report_refinement'" in messages[0]


def test_report_producers_guard_flags_a_contract_dropped_from_the_schema_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _review_producer_repo(tmp_path, schemas='"PreliminarySummary", "AnalysisAgentOutput", "RefinedReport"')
    messages = [v.message for v in qg._reviews_report_producers_create_review_request()]
    assert len(messages) == 1 and "'DecisionReportV1'" in messages[0]


def test_report_producers_guard_flags_finalize_no_longer_staging_reviews(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _review_producer_repo(tmp_path, finalize=False)
    messages = [v.message for v in qg._reviews_report_producers_create_review_request()]
    assert any("report_stages(" in m for m in messages)
    assert any("stage_run_review_request(" in m for m in messages)


def test_report_producers_guard_passes_on_the_real_repo() -> None:
    assert qg._reviews_report_producers_create_review_request() == []


_ENVELOPED_ROUTE = '''
@router.get("/{run_id}/summary")
async def summary(run_id, response, db):
    result = await get_run_mode_summary(run_id, "executive", db, None)
    envelope = await review_envelope_for_run(db, run_id)
    return {**result, **envelope.fields()}
'''

_BARE_ROUTE = '''
@router.get("/{run_id}/export")
async def export(run_id, db):
    intelligence = await get_run_intelligence(run_id, db, None)
    return {"summary": intelligence}


async def helper(run_id, db):
    return await get_run_intelligence(run_id, db, None)
'''


def test_report_consumers_guard_flags_a_route_without_the_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "routers" / "good.py", _ENVELOPED_ROUTE)
    _write(tmp_path / "backend" / "app" / "routers" / "bad.py", _BARE_ROUTE)
    violations = qg._reviews_report_consumers_carry_review_block()
    assert [(v.file.name, v.message.split("'")[1]) for v in violations] == [("bad.py", "export")]


_MCP_TOOLS = '''
def register(mcp):
    @mcp.tool()
    async def get_run_summary(run_id):
        data = await api.get(f"/api/v1/runs/{run_id}/summary")
        return ", ".join([str(data), *review_notice.review_lines(data)])

    @mcp.tool()
    async def check_run_release_readiness(run_id):
        data = await api.get(f"/api/v1/release-readiness/{run_id}")
        return str(data)

    @mcp.tool()
    async def get_run_intelligence(run_id):
        data = await api.get(f"/api/v1/runs/{run_id}/intelligence")
        return "review: " + str(data.get("review"))

    @mcp.tool()
    async def refresh_intelligence(run_id):
        await api.post(f"/api/v1/runs/{run_id}/intelligence/refresh")
        return "ok"
'''


def test_report_consumers_guard_flags_an_mcp_tool_without_review_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "mcp" / "tools" / "reports.py", _MCP_TOOLS)
    violations = qg._reviews_report_consumers_carry_review_block()
    # Mentioning the review block is not appending review_state.
    assert [v.message.split("'")[1] for v in violations] == ["check_run_release_readiness", "get_run_intelligence"]


def test_report_consumers_guard_passes_on_the_real_repo() -> None:
    assert qg._reviews_report_consumers_carry_review_block() == []


# -- agents.catalog-schema-complete (architecture E1.1) ---------------------------


def _catalog_repo(tmp_path: Path, *, capabilities: str, modules: str = '("app.models.evidence_contracts",)') -> None:
    _write(
        tmp_path / "backend" / "app" / "services" / "agent_capability_registry.py",
        "_SPECS = (\n" + capabilities + ")\n",
    )
    _write(
        tmp_path / "backend" / "app" / "services" / "agent_catalog.py",
        f"CATALOG_SCHEMA_MODULES = {modules}\n",
    )
    _write(
        tmp_path / "backend" / "app" / "models" / "evidence_contracts.py",
        "class RunEvidenceBundleV1(BaseModel):\n    pass\n\n\nclass AgentFindingV1(BaseModel):\n    pass\n",
    )


def test_catalog_schema_guard_passes_capabilities_with_real_input_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _catalog_repo(
        tmp_path,
        capabilities=(
            '    _capability("summary", inputs="RunEvidenceBundleV1", output="X"),\n'
            '    _capability("synthesis", inputs="AgentFindingV1[]", output="Y"),\n'
        ),
    )
    assert qg._agents_catalog_schema_complete() == []


def test_catalog_schema_guard_flags_an_input_that_is_only_a_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _catalog_repo(
        tmp_path,
        capabilities=(
            '    _capability("summary", inputs="RunEvidenceBundleV1", output="X"),\n'
            '    _capability("ingestion", inputs="TestRun", output="RunEvidenceBundleV1"),\n'
        ),
    )
    violations = qg._agents_catalog_schema_complete()
    assert [(v.line, v.message.split("'")[1]) for v in violations] == [(3, "ingestion")]


def test_catalog_schema_guard_ignores_models_outside_the_listed_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _catalog_repo(
        tmp_path,
        capabilities='    _capability("summary", inputs="RunEvidenceBundleV1", output="X"),\n',
        modules='("app.models.other",)',
    )
    messages = [v.message for v in qg._agents_catalog_schema_complete()]
    assert any("app.models.other" in m for m in messages)
    assert any("'summary'" in m for m in messages)


def test_catalog_schema_guard_flags_a_missing_module_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _catalog_repo(tmp_path, capabilities='    _capability("summary", inputs="RunEvidenceBundleV1", output="X"),\n', modules="()")
    messages = [v.message for v in qg._agents_catalog_schema_complete()]
    assert messages and "CATALOG_SCHEMA_MODULES not found" in messages[0]


# -- agents.agent-mode-single-writer (architecture E4.4) --------------------------


def test_mode_writer_guard_flags_a_config_row_mode_assignment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "policy_service.py", """
        from app.models.postgres import AgentConfig

        async def upsert(db, project_id, mode):
            row = AgentConfig(project_id=project_id)
            row.mode = mode
            fetched = await load(db, AgentConfig)
            fetched.mode = "act"
    """)
    violations = qg._agents_agent_mode_single_writer()
    assert [(v.line, v.message) for v in violations] == [
        (5, "row.mode assigned outside agent_config_service"),
        (7, "fetched.mode assigned outside agent_config_service"),
    ]


def test_mode_writer_guard_flags_constructor_values_and_raw_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "agents" / "sneaky.py", """
        from sqlalchemy import update
        from app.models.postgres import AgentConfig

        def build(project_id):
            return AgentConfig(project_id=project_id, mode="act")

        async def promote(db):
            await db.execute(update(AgentConfig).values(mode="act"))
            await db.execute(update(AgentConfig).values({"mode": "act"}))

        SQL = "UPDATE agent_configs SET mode = 'act' WHERE agent_id = 'fixer'"
    """)
    by_line = {v.line: v.message for v in qg._agents_agent_mode_single_writer()}
    assert sorted(by_line) == [5, 8, 9, 11]
    assert "AgentConfig(mode=...)" in by_line[5]
    assert ".values(mode=...)" in by_line[8] and ".values(mode=...)" in by_line[9]
    assert "raw SQL" in by_line[11]


def test_mode_writer_guard_allows_the_config_service_and_ignores_other_modes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _redirect_repo_root(monkeypatch, tmp_path)
    _write(tmp_path / "backend" / "app" / "services" / "agent_config_service.py", """
        from app.models.postgres import AgentConfig

        def put(row, mode):
            row.mode = mode
            return AgentConfig(mode=mode)
    """)
    _write(tmp_path / "backend" / "app" / "services" / "unrelated.py", """
        from app.models.postgres import AgentRun

        class RetryPlan:
            def __init__(self, mode):
                self.mode = mode

        def ledger(row, mode):
            row.mode = mode
            return AgentRun(mode=mode)
    """)
    assert qg._agents_agent_mode_single_writer() == []


def test_mode_writer_guard_on_the_real_repo_has_no_exceptions() -> None:
    assert qg._agents_agent_mode_single_writer() == []


def test_codacy_workflow_does_not_reference_secrets_in_step_condition() -> None:
    """GitHub rejects workflows that read ``secrets`` directly from ``if``."""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/codacy.yml"
    ).read_text(encoding="utf-8")

    assert "CODACY_TOKEN_AVAILABLE: ${{ secrets.CODACY_PROJECT_TOKEN != '' }}" in workflow
    assert "if: env.CODACY_TOKEN_AVAILABLE == 'true'" in workflow
    assert not any(
        line.lstrip().startswith("if:") and "secrets." in line
        for line in workflow.splitlines()
    )


def test_prompt_attestation_watched_paths_are_pinned(tmp_path: Path) -> None:
    expected = {
        "backend/app/services/llm_factory.py",
        "backend/app/services/model_router.py",
        "backend/app/services/agent_capability_registry.py",
        "backend/app/agents/reviewer_agent.py",
    }
    assert set(qg._EVAL_ATTESTATION_WATCHED_PATHS) == expected

    target = tmp_path / "backend" / "app" / "services" / "model_router.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    before = qg._eval_attestation_watched_sources(tmp_path)
    target.write_text("after\n", encoding="utf-8")
    after = qg._eval_attestation_watched_sources(tmp_path)
    assert before["backend/app/services/model_router.py"] != after[
        "backend/app/services/model_router.py"
    ]


def test_prompt_attestation_hash_normalizes_checkout_line_endings(tmp_path: Path) -> None:
    relative = qg._EVAL_ATTESTATION_WATCHED_PATHS[0]
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"first\nsecond\n")
    lf_hash = qg._eval_attestation_watched_sources(tmp_path)[relative]
    target.write_bytes(b"first\r\nsecond\r\n")

    assert qg._eval_attestation_watched_sources(tmp_path)[relative] == lf_hash
