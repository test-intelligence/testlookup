"""Shared verdict vocabulary for every agent evaluation surface."""
from __future__ import annotations

from enum import Enum


class EvalVerdict(str, Enum):
    """A conclusive pass/fail, or an honest absence of enough evidence."""

    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_SAMPLES = "insufficient_samples"
