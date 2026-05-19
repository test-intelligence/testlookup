"""Conftest for the regression suite.

Auto-tags every test under ``tests/regression/`` with the ``regression``
marker so CI can run them as a group:

    pytest -m regression

The package's own ``__init__.py`` documents the convention; this
conftest enforces it. Add file-local autouse fixtures here only if a
genuine cross-cutting need appears — start lean.
"""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    """Apply the ``regression`` marker to every collected item."""
    regression_mark = pytest.mark.regression
    for item in items:
        # Only tag items that live under tests/regression/. Defensive
        # because conftest.py at parent levels can also be picked up
        # for unrelated suites in certain monkey-patched layouts.
        if "tests/regression/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(regression_mark)
