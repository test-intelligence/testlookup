from __future__ import annotations

import ast
from pathlib import Path


def test_reviewer_quality_migration_is_the_next_reversible_head() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0182_ai_eval_reviewer_quality.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }

    assert assignments == {"revision": "0182", "down_revision": "0181"}
    assert 'TABLE = "ai_eval_reviewer_quality"' in source
    assert "op.create_table(" in source
    assert "op.create_index(" in source
    assert "def downgrade()" in source
    assert "op.drop_table(TABLE)" in source
