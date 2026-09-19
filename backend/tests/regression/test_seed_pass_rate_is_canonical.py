"""The demo seeders must use the product's own pass-rate rule.

JR-02 / TL-2026-09-19-01-001. Both seeders computed ``passed / total``, the
formula ``app.services.ingestion`` had already abandoned. Measured on the local
stack: **28 of 294** seeded runs stored a pass rate the application calls wrong,
understated by up to **8.47 percentage points**.

What identified the denominator as the cause, rather than something else: all 28
diverging runs had ``skipped_tests > 0``, and **zero** runs without skips
diverged. A fresh ingest of a 6-passed / 2-failed / 2-skipped file through
``POST /api/v1/ingest/file`` stored ``75`` -- the canonical value -- proving
ingestion was right and only the seeders were wrong.

Why this matters more than "demo data is a bit off": seeded rows are the
baseline a developer or evaluator reads first, and a wrong number there is what
*hides* a real regression. TL-2026-09-18-01-005 (every ingested run carrying a
NULL duration) stayed invisible precisely because the seeder filled the column
ingestion had stopped writing.

There is deliberately no browser E2E test here. A Playwright spec cannot see a
seeder's arithmetic -- it can only observe whatever numbers happen to be in the
database, which is the very thing that concealed this. The instrument that
actually prevents recurrence is the source guard below, so that is where the
coverage went.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.core.pass_rate import canonical_pass_rate, executed_count

# ``parents[2]`` is the backend package root (``backend/`` locally, ``/app`` in
# the image), so ``scripts/`` resolves the same in both. Anchoring on the repo
# root instead would break in-container, where there is no ``backend/`` segment.
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
SEEDERS = (
    BACKEND_ROOT / "scripts" / "seed_data.py",
    BACKEND_ROOT / "scripts" / "seed_dev_data.py",
)

# ``passed / total``, ``passed/total``, ``passed / total_tests`` -- any pass-rate
# denominator that counts tests which never executed.
FORBIDDEN_DENOMINATOR = re.compile(r"passed\s*/\s*total\b")


class TestTheCanonicalRule:
    def test_skipped_tests_are_not_in_the_denominator(self):
        # The exact mix ingested live: 6 passed, 2 failed, 2 skipped.
        # Canonical 6/8 = 75.0. The old seeder formula 6/10 = 60.0.
        # The two differ, so this assertion can tell them apart.
        assert canonical_pass_rate(6, 2, 0) == 75.0

    def test_the_old_formula_would_have_failed_this(self):
        # Stated explicitly so the test documents the defect it pins rather
        # than merely asserting today's number.
        passed, failed, skipped = 6, 2, 2
        total = passed + failed + skipped
        old = round(passed / total * 100, 2)
        assert old == 60.0, "the defect's own arithmetic changed"
        assert canonical_pass_rate(passed, failed, 0) != old

    def test_broken_counts_as_executed(self):
        # A BROKEN test ran and did not pass, so it belongs in the denominator
        # but not the numerator: 6/(6+2+2) = 60.0.
        assert canonical_pass_rate(6, 2, 2) == 60.0
        assert executed_count(6, 2, 2) == 10

    def test_a_run_of_only_skips_has_no_pass_rate(self):
        # Must not read as 100%: "we tested nothing" is not "everything passed".
        assert canonical_pass_rate(0, 0, 0) == 0.0

    def test_ndigits_matches_the_summary_report(self):
        # summary_report_service rounds to 1 decimal; measured live as 79.4.
        assert canonical_pass_rate(85, 21, 1, ndigits=1) == 79.4


class TestNoSeederRestatesTheRule:
    def test_the_seeder_files_are_present(self):
        # Without this the guard below passes vacuously if a file is renamed.
        for path in SEEDERS:
            assert path.is_file(), f"seeder not found: {path}"

    @pytest.mark.parametrize("path", SEEDERS, ids=lambda p: p.name)
    def test_no_seeder_divides_a_pass_rate_by_total(self, path: pathlib.Path):
        source = path.read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            # Skip the explanatory prose in docstrings/comments, which names the
            # bad formula on purpose.
            if FORBIDDEN_DENOMINATOR.search(line)
            and not line.lstrip().startswith(("#", "*"))
            and "``" not in line
        ]
        assert offenders == [], (
            f"{path.name} restates the pass-rate rule with a denominator that "
            "includes skipped tests. Import canonical_pass_rate from "
            "app.core.pass_rate instead."
        )

    @pytest.mark.parametrize("path", SEEDERS, ids=lambda p: p.name)
    def test_each_seeder_uses_the_shared_helper(self, path: pathlib.Path):
        source = path.read_text(encoding="utf-8")
        assert "from app.core.pass_rate import canonical_pass_rate" in source, (
            f"{path.name} must import the canonical rule rather than restate it"
        )

    def test_the_guard_can_actually_see_a_violation(self):
        # A guard that has never been shown to fire is not a guard. Feed it the
        # exact line that was removed from seed_data.py.
        reintroduced = "        pass_rate = round(passed / total * 100, 2) if total else 0"
        assert FORBIDDEN_DENOMINATOR.search(reintroduced), (
            "the guard's pattern no longer matches the original defect"
        )

    def test_the_guard_does_not_fire_on_unrelated_division(self):
        # ``skipped / total`` is a legitimate composition rate -- the guard must
        # not force it through the pass-rate helper.
        assert FORBIDDEN_DENOMINATOR.search("skip_rate = skipped / total") is None
