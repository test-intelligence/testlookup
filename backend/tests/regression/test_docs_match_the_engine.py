"""The in-app documentation must state the constants the engine actually uses.

The docs page (B-2) tells users that flakiness weights are 0.45 / 0.25 / 0.20 /
0.10, that five observations are needed before a score exists, and that the
release gate hard-floors at 70% of the configured bar. Those are not
illustrations — they are the real values, read out of the implementation.

A doc page that keeps quoting a threshold the engine no longer uses is the same
defect class this codebase keeps producing: something published to a reader
that nothing in the system produces. `_fallback_used`, `events_received` and
`anthropic_key_set` were all that shape. Prose is harder to notice, not less
wrong — nobody gets a stack trace from a stale sentence.

So the constants are asserted from BOTH sides. The frontend test
(`frontend/src/pages/DocsPage.test.tsx`) pins that the page renders them; this
one pins that they still match Python. Both now read
`frontend/src/content/docs/*.md`, which is what the page renders. Change a weight in the engine and this
fails until the documentation is updated too.
"""
from __future__ import annotations

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
# The prose moved out of the page component into content files. DocsPage.tsx is
# now the shell — nav, routing, Markdown rendering — and holds no documentation
# text, so asserting against it would pass vacuously forever. Read what the page
# actually renders instead.
# NOT "docs" — `.gitignore` carries a bare `docs/` rule that matches at every
# depth, so a directory of that name anywhere in the tree is silently
# excluded from every commit. The content lived there briefly and was
# invisible to git and to the Mermaid validator until the guard caught it.
DOCS_CONTENT = REPO / "frontend" / "src" / "content" / "guide"

pytestmark = pytest.mark.skipif(
    not DOCS_CONTENT.is_dir(), reason="frontend not present in this checkout"
)


def _docs() -> str:
    """Every documentation page the app renders, concatenated.

    Concatenated rather than per-file because these assertions are about the
    documentation as a whole: the reader does not care which file states the
    observation floor, only that the guide does.
    """
    raw = chr(10).join(
        sorted(p.read_text(encoding="utf-8") for p in DOCS_CONTENT.glob("*.md"))
    )
    # Strip emphasis markers before matching. These assertions are about what
    # the READER sees; "Fewer than **5**" renders as "Fewer than 5", and a
    # content check that a bold marker can defeat is checking formatting, not
    # content.
    return raw.replace("**", "").replace("`", "")


def test_the_docs_page_is_readable_and_non_trivial():
    """Every assertion below is a substring search. If the page moved or shrank
    to a stub they would all pass vacuously."""
    src = _docs()
    assert len(src) > 4_000, f"docs page looks like a stub ({len(src)} bytes)"
    # The three areas whose constants this file pins. Labels changed when the
    # documentation was restructured; these are the subjects, not the headings,
    # so a future rename does not silently empty this guard.
    for marker in ("flaky", "risk dimensions", "advisory, not authoritative"):
        assert marker in src, f"section marker {marker!r} missing"


# ── Flakiness ───────────────────────────────────────────────────────────────


def test_documented_flakiness_weights_match_the_scorer():
    from app.services.flaky_score_service import DEFAULT_WEIGHTS

    src = _docs()
    missing = [
        f"{name}={weight}"
        for name, weight in DEFAULT_WEIGHTS.items()
        if f"{weight:.2f}" not in src
    ]
    assert not missing, (
        "documentation does not state the weights the scorer uses: "
        f"{missing} (engine has {DEFAULT_WEIGHTS})"
    )


def test_documented_weights_still_sum_to_one():
    """The docs present them as a weighted blend. If they stopped summing to 1
    the page's framing would be wrong even with every number copied across."""
    from app.services.flaky_score_service import DEFAULT_WEIGHTS

    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_documented_observation_floor_matches_the_scorer():
    from app.services.flaky_score_service import MIN_OBSERVATIONS

    src = _docs()
    assert f"Fewer than {MIN_OBSERVATIONS}" in src, (
        f"docs do not state the real observation floor ({MIN_OBSERVATIONS})"
    )


def test_documented_confidence_bands_match_the_scorer():
    from app.services.flaky_score_service import (
        LOW_CONFIDENCE_MAX,
        MEDIUM_CONFIDENCE_MAX,
        MIN_OBSERVATIONS,
    )

    src = _docs()
    assert f"{MIN_OBSERVATIONS} – {LOW_CONFIDENCE_MAX}" in src
    assert f"{LOW_CONFIDENCE_MAX + 1} – {MEDIUM_CONFIDENCE_MAX}" in src
    assert f"{MEDIUM_CONFIDENCE_MAX + 1} or more" in src


def test_the_docs_do_not_promise_a_score_below_the_floor():
    """The engine returns score=None below MIN_OBSERVATIONS. Documenting that as
    0.0 would invent stability nobody measured."""
    src = _docs()
    assert "insufficient" in src.lower()


# ── Release gate ────────────────────────────────────────────────────────────


def test_documented_hard_floor_matches_the_engine():
    from app.services.criticality_service import HARD_FLOOR_FACTOR

    src = _docs()
    pct = int(round(HARD_FLOOR_FACTOR * 100))
    assert f"{pct}% of your configured bar" in src, (
        f"docs do not state the real hard-floor factor ({HARD_FLOOR_FACTOR})"
    )


def test_every_risk_dimension_is_documented():
    """Seven dimensions drive the composite. One missing from the page means a
    user cannot account for part of their own score."""
    from app.services.criticality_service import _weights

    # Engine key -> the phrase the page uses for it.
    labels = {
        "user_impact": "User impact",
        "env_sensitivity": "Environment sensitivity",
        "reproducibility": "Reproducibility",
        "regression_likely": "Regression likelihood",
        "hist_recurrence": "Historical recurrence",
        "blast_radius": "Blast radius",
        "diagnosis_conf": "Diagnosis confidence",
    }
    src = _docs()
    dimensions = set(_weights())
    unmapped = sorted(dimensions - set(labels))
    assert not unmapped, f"new risk dimension with no documented label: {unmapped}"

    missing = sorted(k for k in dimensions if labels[k] not in src)
    assert not missing, f"risk dimensions absent from the documentation: {missing}"


def test_the_documented_verdicts_are_the_ones_the_engine_can_return():
    """All three states must be described — CONDITIONAL_GO in particular was
    once unreachable, and a page that omitted it would have looked correct."""
    src = _docs().upper().replace("-", "_").replace(" ", "_")
    for verdict in ("GO", "CONDITIONAL_GO", "NO_GO"):
        assert verdict in src, f"documentation never mentions {verdict}"


def test_the_docs_state_that_bands_only_tighten():
    """release_council_service layers bands fail-closed: they can downgrade a
    verdict but never upgrade one. A reader who assumes otherwise will
    mis-predict their own gate."""
    src = _docs().lower()
    assert "never unblock" in src or "can never unblock" in src


# ── The content must actually be in the repository ──────────────────────────


def test_every_documentation_file_is_tracked_by_git():
    """A content file on disk but not in git is invisible to CI.

    The failure this exists for: ``.gitignore`` ignores ``AGENTS.md`` at every
    depth (coding-agent instruction files, deliberately). A case-insensitive
    checkout — every Windows clone — matches ``agents.md`` too, so the page's
    agent documentation sat on disk, rendered locally and passed every local
    test while never being committed. CI checked out 18 of 19 content files and
    failed there and only there.

    Disk-vs-git, not disk-vs-registry: the registry check cannot see this,
    because locally the file is present in both.

    This lives in the Python suite rather than the frontend one because the
    frontend build runs ``tsc`` over its tests, and Node built-ins are not typed
    there — the sibling promotion regressions avoid them for the same reason.
    """
    import subprocess

    on_disk = sorted(p.name for p in DOCS_CONTENT.glob("*.md"))
    assert len(on_disk) > 15, (
        f"only {len(on_disk)} content files found — this check would pass vacuously"
    )

    proc = subprocess.run(
        ["git", "ls-files", "--", "*.md"],
        cwd=DOCS_CONTENT, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:  # pragma: no cover - not a git checkout
        pytest.skip("git unavailable")
    tracked = {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    untracked = [name for name in on_disk if name not in tracked]
    assert not untracked, (
        "documentation files on disk but not in git — CI will never see them, "
        f"and neither will the Mermaid validator: {untracked}"
    )
