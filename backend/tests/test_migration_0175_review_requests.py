"""Migration 0175 (E8.1): review_requests, users.is_synthetic, a draft-distribution setting.

A migration must not import application code, so it carries its own frozen
copy of the review vocabularies. These tests hold that copy in step with the
ORM model: a state added to one and not the other would make the database
reject a value the code writes, or accept one the code never expects.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app.models.postgres import (
    REVIEW_KINDS,
    REVIEW_REASON_CODES,
    REVIEW_STATES,
    REVIEW_SUBJECT_TYPES,
    Project,
    ReviewRequest,
    User,
)

MIGRATION = Path(__file__).resolve().parents[1] / "migrations/versions/0175_review_requests.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _module_constants() -> dict:
    tree = ast.parse(_source())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        found[target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        pass
    return found


def test_it_chains_after_0174():
    source = _source()
    assert 'revision = "0175"' in source
    assert 'down_revision = "0174"' in source


def test_the_frozen_vocabularies_match_the_model():
    constants = _module_constants()
    assert tuple(constants["KINDS"]) == REVIEW_KINDS
    assert tuple(constants["SUBJECT_TYPES"]) == REVIEW_SUBJECT_TYPES
    assert tuple(constants["STATES"]) == REVIEW_STATES
    assert tuple(constants["REASON_CODES"]) == REVIEW_REASON_CODES


def test_the_architecture_vocabulary_is_what_shipped():
    """Section 8.1 names these exactly; a rename here breaks E8.2's contract."""
    assert set(REVIEW_STATES) == {"pending_review", "accepted", "rejected", "superseded"}
    assert set(REVIEW_REASON_CODES) == {
        "wrong_category", "unsupported_claim", "missing_evidence",
        "contradiction", "stale_data", "other",
    }


def test_every_check_constraint_is_declared_on_both_sides():
    migration_names = set(re.findall(r'name="(ck_review_requests_[a-z_]+)"', _source()))
    model_names = {
        c.name for c in ReviewRequest.__table__.constraints
        if getattr(c, "name", "") and str(c.name).startswith("ck_review_requests_")
    }
    assert migration_names == model_names
    assert "ck_review_requests_rejection_has_reason" in model_names


def test_the_one_live_request_index_is_partial_on_both_sides():
    assert "uq_review_requests_live_subject" in _source()
    assert "state <> 'superseded'" in _source()
    index = next(i for i in ReviewRequest.__table__.indexes if i.name == "uq_review_requests_live_subject")
    assert index.unique
    assert "superseded" in str(index.dialect_options["postgresql"]["where"])


def test_the_settled_check_does_not_depend_on_a_set_null_column():
    """reviewed_by is SET NULL on user deletion; a CHECK that required it would
    make deleting a reviewer fail on every review they settled."""
    settled = next(
        c for c in ReviewRequest.__table__.constraints
        if getattr(c, "name", "") == "ck_review_requests_settled_has_time"
    )
    assert "reviewed_by" not in str(settled.sqltext)


def test_the_new_columns_exist_on_the_orm():
    assert "is_synthetic" in User.__table__.columns
    assert "allow_unreviewed_distribution" in Project.__table__.columns
    assert User.__table__.columns["is_synthetic"].nullable is False
    assert Project.__table__.columns["allow_unreviewed_distribution"].nullable is False


def test_synthetic_accounts_are_backfilled_from_the_qa_lead_domain():
    from app.services import default_qa_lead_service

    source = _source()
    assert "UPDATE users SET is_synthetic = true" in source
    assert default_qa_lead_service._DEFAULT_QA_LEAD_DOMAIN in source, (
        "the backfill domain must be the one the QA-lead service actually uses"
    )


def test_downgrade_removes_everything_upgrade_adds():
    source = _source()
    downgrade = source[source.index("def downgrade()"):]
    for marker in ('drop_table(TABLE)', '"is_synthetic"', '"allow_unreviewed_distribution"'):
        assert marker in downgrade
