"""FLK-P1 — intermittency + error-signature analysis (pure, no-DB, never-raise).

Discriminates *high-volatility flakiness* (a test that flips pass↔fail with many
distinct errors / in-run framework retries — a classic flake) from
*low-volatility regression* (a test that fails persistently with a single
repeated error — a real break that should NOT be quarantined as "flaky").

All metrics are derived from a per-run window of records; every input is coerced
defensively so a malformed row degrades to a neutral contribution rather than
raising. The granular ``retry_count`` / ``is_flaky_run`` / ``stack_trace``
signals merged in PR #169 strengthen the verdict where present.

This module is pure (no DB, no I/O, no outbound calls) so it is safe under
``AI_OFFLINE_MODE`` by construction and can be reused by both the refresh path
(verdict refinement) and the read path (surfacing).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

# Statuses that count as a "failure" for flip / failure-signature analysis.
_FAILED_STATUSES = {"FAILED", "BROKEN"}

# Volatility at/above this is "intermittent" (classic flake); below it with a
# single dominant error signature looks like a persistent regression.
_HIGH_VOLATILITY = 0.40
_LOW_VOLATILITY = 0.20

# Run-specific noise (hex addresses, long ids, timestamps, bare numbers) that
# would inflate error-signature diversity, normalised away before fingerprinting.
_NOISE = re.compile(
    r"0x[0-9a-fA-F]+"               # hex addresses
    r"|\b[0-9a-fA-F]{8,}\b"         # long hex ids
    r"|\d{4}-\d{2}-\d{2}[T \d:.,+]*"  # iso-ish timestamps
    r"|\d+",                        # any bare number
)


@dataclass(frozen=True)
class IntermittencySignals:
    """Structured flakiness-intermittency signal for one test fingerprint."""

    runs: int = 0
    fail_count: int = 0
    flip_count: int = 0
    status_volatility: float = 0.0          # flips / (runs - 1), 0..1
    error_signature_diversity: float = 0.0  # unique error prefixes / fail_count
    stack_trace_diversity: float = 0.0      # unique stack fingerprints / fail_count
    in_run_retry_rate: float = 0.0          # fraction of runs with in-run retry/flaky flag
    intermittency_label: str = "insufficient_data"

    def to_dict(self) -> dict:
        return asdict(self)


def _norm_status(value: Any) -> str:
    try:
        text = str(value).upper().strip()
    except Exception:
        return "UNKNOWN"
    # Enum reprs like "TestStatus.FAILED" collapse to the member name.
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _is_failed(status: str) -> bool:
    return status in _FAILED_STATUSES


def _error_signature(error_message: Any) -> str:
    """Normalise an error message to a stable, noise-free prefix signature."""
    if not error_message:
        return ""
    try:
        text = str(error_message)
    except Exception:
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    # First line, denoised, lower-cased, capped — captures the error *kind*
    # while collapsing run-specific values that would inflate diversity.
    first_line = stripped.splitlines()[0]
    denoised = _NOISE.sub("#", first_line).lower()
    return denoised[:80]


# Public, stable aliases of the signature helpers so other flaky modules
# (FLK-P4 investigator) can cluster failures without reaching into privates.
def error_signature(error_message: Any) -> str:
    """Public alias of the noise-free error-message signature."""
    return _error_signature(error_message)


def stack_fingerprint(stack_trace: Any) -> str:
    """Public alias of the stack-trace fingerprint."""
    return _stack_fingerprint(stack_trace)


def _stack_fingerprint(stack_trace: Any) -> str:
    """SHA over the first ~500 chars of a stack trace. Many unique fingerprints
    per flaky test => environmental/race; one fingerprint => deterministic bug.
    """
    if not stack_trace:
        return ""
    try:
        text = str(stack_trace)
    except Exception:
        return ""
    head = _NOISE.sub("#", text[:500])
    if not head.strip():
        return ""
    return hashlib.sha256(head.encode("utf-8", "replace")).hexdigest()[:16]


def _coerce_retry_flag(record: Mapping[str, Any]) -> bool:
    """True when the framework recorded an in-run retry or its own flaky flag."""
    try:
        retry = record.get("retry_count")
        if retry is not None and int(retry) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(record.get("is_flaky_run"))


def _label(
    runs: int,
    fail_count: int,
    volatility: float,
    error_diversity: float,
    in_run_retry_rate: float,
) -> str:
    if runs < 3 or fail_count == 0:
        return "insufficient_data"
    # An in-run framework retry is a strong, direct flake signal regardless of
    # cross-run volatility.
    if in_run_retry_rate > 0.0:
        return "intermittent_flaky"
    if volatility >= _HIGH_VOLATILITY:
        # Frequent pass↔fail flips with many distinct errors == environmental
        # noise; with one repeated error it is still an intermittent flake.
        return "environmental_flaky" if error_diversity >= 0.5 else "intermittent_flaky"
    if volatility <= _LOW_VOLATILITY and error_diversity < 0.5:
        # Rarely flips, same error every time => looks like a real regression,
        # not a flake — do not quarantine on the flake track.
        return "persistent_regression"
    return "low_volatility_flaky"


def compute_intermittency_signals(
    records: Iterable[Mapping[str, Any]],
) -> IntermittencySignals:
    """Compute intermittency signals from a per-run window for one fingerprint.

    ``records`` is an iterable of mappings with (all optional) keys:
    ``status``, ``error_message``, ``stack_trace``, ``retry_count``,
    ``is_flaky_run``. Order is the run order (used for adjacency/flip counting).
    Never raises — a malformed record contributes neutrally.
    """
    rows: list[Mapping[str, Any]] = []
    for r in records or []:
        if isinstance(r, Mapping):
            rows.append(r)

    runs = len(rows)
    if runs == 0:
        return IntermittencySignals()

    statuses = [_norm_status(r.get("status")) for r in rows]
    fail_count = sum(1 for s in statuses if _is_failed(s))

    # Flips: adjacent changes in the binary failed/not-failed sequence.
    flip_count = sum(
        1
        for a, b in zip(statuses, statuses[1:])
        if _is_failed(a) != _is_failed(b)
    )
    status_volatility = flip_count / (runs - 1) if runs > 1 else 0.0

    # Error-signature + stack-trace diversity over FAILED rows only.
    error_sigs: set[str] = set()
    stack_fps: set[str] = set()
    failed_with_error = 0
    failed_with_stack = 0
    for r, status in zip(rows, statuses):
        if not _is_failed(status):
            continue
        sig = _error_signature(r.get("error_message"))
        if sig:
            error_sigs.add(sig)
            failed_with_error += 1
        fp = _stack_fingerprint(r.get("stack_trace"))
        if fp:
            stack_fps.add(fp)
            failed_with_stack += 1

    error_signature_diversity = (
        len(error_sigs) / failed_with_error if failed_with_error else 0.0
    )
    stack_trace_diversity = (
        len(stack_fps) / failed_with_stack if failed_with_stack else 0.0
    )

    in_run_retry_rate = (
        sum(1 for r in rows if _coerce_retry_flag(r)) / runs if runs else 0.0
    )

    label = _label(
        runs, fail_count, status_volatility, error_signature_diversity, in_run_retry_rate
    )

    return IntermittencySignals(
        runs=runs,
        fail_count=fail_count,
        flip_count=flip_count,
        status_volatility=round(status_volatility, 3),
        error_signature_diversity=round(error_signature_diversity, 3),
        stack_trace_diversity=round(stack_trace_diversity, 3),
        in_run_retry_rate=round(in_run_retry_rate, 3),
        intermittency_label=label,
    )
