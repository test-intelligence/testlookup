"""``benchmarks/METHODOLOGY.md`` warns that `make dev` leaves SQL echo on. Keep that true.

The warning exists because the echo cost is invisible: nothing in a benchmark's output says
it is on, so anyone measuring locally silently gets inflated latencies (~13% on a search
request, measured). It cost an afternoon to find once.

The warning rests on exactly two facts in the code:

* ``backend/app/db/postgres.py`` builds the engine with ``echo=settings.is_development``;
* ``scripts/gen-dev-env.sh`` writes ``APP_ENV=development`` into the ``.env`` that
  ``make dev`` uses.

If either changes, the document becomes wrong in the more dangerous direction -- it would
still *look* authoritative while describing behaviour the code no longer has. This pins both,
so the doc and the code cannot drift apart silently.

Deliberately asserts the mechanism rather than the measured percentage: the 13% is
hardware-specific and would make this test a treadmill.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
METHODOLOGY = REPO_ROOT / "benchmarks" / "METHODOLOGY.md"
POSTGRES_MODULE = REPO_ROOT / "backend" / "app" / "db" / "postgres.py"
DEV_ENV_SCRIPT = REPO_ROOT / "scripts" / "gen-dev-env.sh"


def test_the_engine_still_enables_echo_in_development():
    """The first half of the warning."""
    source = POSTGRES_MODULE.read_text(encoding="utf-8")

    assert "echo=settings.is_development" in source, (
        "app/db/postgres.py no longer ties SQL echo to development mode. If echo is now "
        "off by default, the 'SQL echo is ON under make dev' section of "
        "benchmarks/METHODOLOGY.md is obsolete and should be removed rather than left "
        "to mislead."
    )


def test_make_dev_still_selects_development_mode():
    """The second half: echo only fires because the generated .env says development."""
    script = DEV_ENV_SCRIPT.read_text(encoding="utf-8")

    assert re.search(r'set_kv\s+APP_ENV\s+"development"', script), (
        "scripts/gen-dev-env.sh no longer sets APP_ENV=development, so `make dev` may no "
        "longer enable SQL echo. Re-check benchmarks/METHODOLOGY.md before assuming its "
        "warning still applies."
    )


def test_the_methodology_still_carries_the_warning():
    """And the document itself still says so -- a code comment nobody reads is not the
    point; the warning has to be where someone about to benchmark will look."""
    doc = METHODOLOGY.read_text(encoding="utf-8")

    assert "echo=settings.is_development" in doc, (
        "METHODOLOGY.md no longer names the setting that causes the inflation"
    )
    assert "APP_ENV=production" in doc, (
        "METHODOLOGY.md no longer tells the reader how to measure without echo"
    )
    # The ordering trap: suppression written before the engine is built is undone.
    assert "AFTER importing" in doc, (
        "METHODOLOGY.md lost the note that logger suppression must come after the import "
        "that builds the engine -- without it the documented workaround silently fails"
    )
