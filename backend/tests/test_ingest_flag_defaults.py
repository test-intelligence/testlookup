"""
Regression tests for migration 0099 (PMF backlog US-1.5): Cypress and
Playwright ingestion must be enabled by default — a fresh install 503ing
on two advertised formats was a first-run trap.

The migration is plain data-UPDATE SQL, so these tests pin its contract
statically (no live DB needed): the flag keys it flips must be exactly the
keys the ingest router gates on, and upgrade()/downgrade() must set
enabled_global true/false respectively. The single-head chain is covered
by the database.single-alembic-head quality gate.
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0099_enable_cypress_playwright_ingest_by_default.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0099", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0099_flips_exactly_the_router_gated_flags():
    module = _load_migration()
    # The ingest router builds gate keys as f"{format}_ingest" for the two
    # gated formats — the migration must flip exactly those keys.
    assert set(module._FLAG_KEYS) == {"cypress_ingest", "playwright_ingest"}
    assert module.revision == "0099"
    assert module.down_revision == "0098"


def test_migration_0099_upgrade_enables_and_downgrade_disables():
    module = _load_migration()
    upgrade_src = inspect.getsource(module.upgrade)
    downgrade_src = inspect.getsource(module.downgrade)
    assert "enabled_global = true" in upgrade_src
    assert "enabled_global = false" in downgrade_src
    # Guard against the classic copy-paste inversion.
    assert "enabled_global = false" not in upgrade_src
    assert "enabled_global = true" not in downgrade_src
