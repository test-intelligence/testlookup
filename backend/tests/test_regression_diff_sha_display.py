"""D-12: a commit's sha in the regression diff is shown as unknown when it is not hex, never cut to "[REDACTE".

Pure: ``regression_diff_service._listed`` over stored ``run_commit_ranges.commits`` items.
"""
from __future__ import annotations

import hashlib

from app.services.redaction_service import REDACTED
from app.services.regression_diff_service import _listed


def test_a_hex_sha_is_listed_short_and_full() -> None:
    full = hashlib.sha1(b"commit 1").hexdigest()
    [item] = _listed([{"sha": full, "message": "m"}])
    assert item["sha"] == full[:8] and item["full_sha"] == full
    [item] = _listed([{"sha": full[:8], "message": "m"}])
    assert item["sha"] == full[:8] and item["full_sha"] == full[:8]


def test_a_redacted_or_non_hex_sha_is_unknown_not_sliced() -> None:
    for stored in (REDACTED, "not-a-sha-at-all", "", None, "abc"):
        [item] = _listed([{"sha": stored, "message": "m"}])
        assert item["sha"] == "" and item["full_sha"] is None, stored
        assert "[REDACTE" not in repr(item)
