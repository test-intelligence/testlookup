"""Pure partition logic for ``testlookup ci-verdict`` (US-5.2).

Deliberately stdlib-only (no typer / httpx / platformdirs imports) so the
backend test suite can load this file directly by path
(``backend/tests/test_ci_verdict_logic.py``) without installing the CLI
package — the same convention as ``ci_context.py``.

Matching contract
-----------------

A failed test is *quarantined* (suppressed) when it matches a manifest entry
by, in order of preference:

1. **Fingerprint** — the server's canonical test identity,
   ``sha256(f"{class_name or ''}::{test_name}")[:16]``. This mirrors the
   backend's ``make_test_fingerprint`` in ``app/services/ingestion.py``
   exactly (a regression test pins the two against each other). The tests
   API doesn't return the fingerprint, so we recompute it client-side from
   ``class_name`` + ``test_name``.
2. **Name tuple fallback** — ``(test_name, suite_name)`` exact match after
   whitespace normalisation. Covers producers whose class_name at report
   time differs from what the quarantine row was created with.

Anything unmatched is a *real* failure.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

# Exit-code contract for ci-verdict (documented in --help):
EXIT_PASS = 0           # no real failures (quarantined-only failures are OK)
EXIT_REAL_FAILURES = 1  # at least one non-quarantined failure (or --strict + any failure)
EXIT_INFRA_ERROR = 2    # API/network error — fail CLOSED unless --fail-open

# Test-case statuses that count as a failure for verdict purposes.
FAILURE_STATUSES = ("FAILED", "BROKEN")


def compute_fingerprint(test_name: str, class_name: Optional[str]) -> str:
    """Client-side copy of the backend's ``make_test_fingerprint``."""
    key = f"{class_name or ''}::{test_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _norm(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def partition_failures(
    failures: list[dict],
    manifest_entries: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split a run's failed tests into ``(real, quarantined)`` lists.

    ``failures`` are rows from ``GET /runs/{id}/tests`` (need ``test_name``;
    ``class_name`` / ``suite_name`` optional). ``manifest_entries`` are rows
    from the quarantine manifest (``fingerprint`` primary, name fields for
    the fallback). Each returned failure dict gains a ``matched_by`` key on
    the quarantined side (``"fingerprint"`` or ``"name"``).
    """
    fingerprints = {
        _norm(e.get("fingerprint"))
        for e in manifest_entries
        if _norm(e.get("fingerprint"))
    }
    name_pairs = {
        (_norm(e.get("test_name")), _norm(e.get("suite_name")))
        for e in manifest_entries
        if _norm(e.get("test_name"))
    }

    real: list[dict] = []
    quarantined: list[dict] = []
    for failure in failures:
        test_name = _norm(failure.get("test_name"))
        fp = _norm(failure.get("test_fingerprint")) or compute_fingerprint(
            test_name, failure.get("class_name") or None
        )
        if fp in fingerprints:
            quarantined.append({**failure, "matched_by": "fingerprint"})
        elif name_pairs and (test_name, _norm(failure.get("suite_name"))) in name_pairs:
            quarantined.append({**failure, "matched_by": "name"})
        else:
            real.append(failure)
    return real, quarantined


def verdict_exit_code(
    real_count: int,
    quarantined_count: int,
    strict: bool = False,
) -> int:
    """Exit code per the ci-verdict contract.

    * 0 — no real failures. Quarantined failures do not block (that is the
      whole point) unless ``strict``.
    * 1 — any real failure; with ``strict``, ANY failure incl. quarantined.
    """
    if real_count > 0:
        return EXIT_REAL_FAILURES
    if strict and quarantined_count > 0:
        return EXIT_REAL_FAILURES
    return EXIT_PASS


def build_verdict(
    real: list[dict],
    quarantined: list[dict],
    strict: bool = False,
) -> dict:
    """Machine-readable verdict document for ``--output json``."""
    code = verdict_exit_code(len(real), len(quarantined), strict=strict)
    return {
        "verdict": "pass" if code == EXIT_PASS else "fail",
        "exit_code": code,
        "strict": strict,
        "counts": {
            "real_failures": len(real),
            "quarantined_failures": len(quarantined),
            "total_failures": len(real) + len(quarantined),
        },
        "real_failures": real,
        "quarantined_failures": quarantined,
    }
