"""The tool-contract snapshots compare an MCPServer tool's published JSON; its ``description`` is the tool function's
docstring. Python 3.13 strips the common indentation from every docstring at compile time, so the SAME docstring
publishes as indented text on 3.11 / 3.12 and as dedented text on 3.13+ (W41 moved the image and CI to 3.14). The
wording is the contract; that indentation is not. ``canonical`` normalises every ``description`` with
``inspect.cleandoc`` (what 3.13+ does, plus outer blank lines), so a snapshot holds on every supported Python and still
fails on any change of wording.
"""
from __future__ import annotations

import copy
import inspect
from typing import Any


def canonical(contract: Any) -> Any:
    """A deep copy of ``contract`` with every string ``description`` passed through ``inspect.cleandoc``."""
    out = copy.deepcopy(contract)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    node[key] = inspect.cleandoc(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(out)
    return out
