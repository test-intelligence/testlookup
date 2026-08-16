"""Regression: the health-check status vocabulary is shared across two languages.

Bug (homelab, 2026-08-16): ``/health/details`` reported

    "ollama": {"status": "skipped", "detail": "AI_OFFLINE_MODE=false — using cloud LLM"}

and an overall ``"status": "healthy"``. The SPA's ``useSystemHealth`` counted
every check whose status was not exactly ``"ok"`` as unavailable, so a fully
working OpenRouter deployment displayed a permanent banner reading
"Degraded: ollama unreachable" — contradicting the backend's own verdict in the
same payload, and pointing operators at a service that was deliberately not
being probed.

The class: **a producer grows a value and a consumer renders from an older
vocabulary.** Same shape as the settings page hardcoding six of seven LLM
providers (#617) and ``model_status_service`` not knowing ``openrouter``.

Python cannot import the TypeScript, so this pins the two sides against each
other textually: every status literal the backend can emit must be explicitly
classified by the frontend as benign or not. Adding a status on either side
fails this test and forces the decision to be made, instead of the new value
silently landing in whichever bucket the default happens to be.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
HEALTH_PY = REPO / "backend" / "app" / "routers" / "health.py"
HOOK_TS = REPO / "frontend" / "src" / "hooks" / "useSystemHealth.ts"

pytestmark = pytest.mark.skipif(
    not HOOK_TS.exists(), reason="frontend not present in this checkout"
)

# Statuses that mean a real problem. Kept here rather than derived so that a
# new backend status cannot quietly be assumed harmless.
KNOWN_FAILING = {"degraded", "error", "timeout", "unavailable"}


def _backend_statuses() -> set[str]:
    """Every ``"status": "<literal>"`` a per-check probe can return.

    Scoped to the ``_check_*`` / ``_with_budget`` functions on purpose. The
    router also emits envelope and liveness statuses ("healthy", "ready",
    "alive"), but ``useSystemHealth`` only iterates ``checks`` — folding those
    in would make this guard fail for values the consumer never sees.
    """
    text = HEALTH_PY.read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=(?:async )?def )", text)
    probes = [
        b for b in blocks
        if re.match(r"(?:async )?def (_check_\w+|_with_budget)\b", b)
    ]
    assert probes, "found no probe functions in health.py"
    return set(re.findall(r'"status":\s*"([a-z_]+)"', "\n".join(probes)))


def _frontend_benign() -> set[str]:
    """The BENIGN_STATUSES set literal out of the hook."""
    text = HOOK_TS.read_text(encoding="utf-8")
    m = re.search(r"BENIGN_STATUSES[^=]*=\s*new Set\(\[([^\]]*)\]\)", text)
    assert m, "could not find the BENIGN_STATUSES set in useSystemHealth.ts"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_both_sides_were_actually_parsed():
    """Every assertion below is a set operation; empty scrapes pass vacuously."""
    backend, benign = _backend_statuses(), _frontend_benign()
    assert "ok" in backend, f"did not parse statuses out of health.py: {backend}"
    assert benign, "did not parse BENIGN_STATUSES out of useSystemHealth.ts"


def test_skipped_does_not_count_as_a_failure():
    """The regression itself. `skipped` is a deliberate non-check."""
    assert "skipped" in _frontend_benign(), (
        "'skipped' is not in the frontend's benign set, so a deliberately "
        "un-probed service renders as a degraded-system banner"
    )


def test_every_backend_status_is_explicitly_classified():
    """The class guard: no status may be left to the default bucket."""
    unclassified = _backend_statuses() - _frontend_benign() - KNOWN_FAILING
    assert not unclassified, (
        f"health.py can emit {sorted(unclassified)}, which the frontend neither "
        f"treats as benign nor this test knows to be a failure. Decide which it "
        f"is: add it to BENIGN_STATUSES in useSystemHealth.ts, or to "
        f"KNOWN_FAILING here."
    )


def test_real_failures_are_not_marked_benign():
    """The fix must not be over-applied into silence."""
    wrongly_benign = _frontend_benign() & KNOWN_FAILING
    assert not wrongly_benign, (
        f"{sorted(wrongly_benign)} are genuine failures but the frontend treats "
        f"them as benign — the degraded banner would never appear"
    )


def test_the_ollama_skip_reports_a_skip_not_a_failure():
    """Pin the emitting side too, so the contract can't be broken from either
    end: if this probe started returning `degraded` for a cloud-LLM deployment,
    the banner would come back and this file would still pass."""
    text = HEALTH_PY.read_text(encoding="utf-8")
    m = re.search(
        r"if not settings\.AI_OFFLINE_MODE:\s*\n\s*return \{\"status\": \"([a-z_]+)\"",
        text,
    )
    assert m, "the AI_OFFLINE_MODE branch of the Ollama probe has moved"
    assert m.group(1) == "skipped", (
        f"the Ollama probe reports {m.group(1)!r} when a cloud LLM is configured; "
        f"it must report 'skipped' — not probing something is not the same as "
        f"that thing being broken"
    )
