"""W16 AGT-09 (b): the duplicate detector's decision core, extracted.

``_find_duplicate_semantic``'s difflib fallback now calls the pure
``title_duplicate_ratio`` (the AI-eval replay judges golden title PAIRS with it
and never touches a project's ChromaDB collection). The fallback must answer
byte-identically to the code it replaced: the pre-W16 loop is copied below
verbatim as the reference and compared on ten title pairs.
"""
from __future__ import annotations

import difflib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import defect_promotion_service as svc
from app.services.golden_datasets import get_golden_duplicate_detection_items

PROJECT = "00000000-0000-0000-0000-0000000000a1"


def _pre_w16_fallback(duplicate_hint: str, open_defects: list) -> tuple[str | None, bool]:
    """The fallback as it stood before W16 (defect_promotion_service.py:773-783)."""
    hint_lower = duplicate_hint.lower()
    for d in open_defects:
        candidate_title = (d.title or d.cluster_id or "").lower()
        ratio = difflib.SequenceMatcher(
            None, hint_lower[:100], candidate_title[:100]
        ).ratio()
        if ratio > 0.7:
            return str(d.id), True
    return None, False


def _pairs() -> list[tuple[str, str | None, str]]:
    golden = [(i["input"]["title_a"], i["input"]["title_b"], "") for i in get_golden_duplicate_detection_items()]
    return golden + [
        ("Checkout returns 500 on submit", "checkout returns 500 on submit", ""),
        ("Checkout returns 500 on submit!", None, "Checkout returns 500 on submit"),  # cluster_id fallback
        ("x" * 99 + "İSTANBUL timeout", "x" * 99 + "istanbul timeout", ""),  # lower() changes the length
        ("Login page timeout on checkout flow" * 4, "Login page timeout on checkout flow" * 3 + "Z" * 40, ""),
    ]


def _defect(title, cluster_id):
    d = MagicMock()
    d.id = uuid.uuid4()
    d.title = title
    d.cluster_id = cluster_id
    return d


@pytest.fixture
def no_memory_no_chroma(monkeypatch):
    async def memory_down(*_a, **_k):
        raise RuntimeError("memory store down")

    async def chroma_down(*_a, **_k):
        raise RuntimeError("chroma unavailable")

    monkeypatch.setattr("app.services.agent_memory_service.find_duplicate_defect_memory", memory_down)
    monkeypatch.setattr("app.services.storage_config_service.get_effective_storage_config", chroma_down)
    monkeypatch.setattr("app.services.chroma_collections.resolve", chroma_down)


def test_there_are_ten_pairs():
    assert len(_pairs()) == 10


@pytest.mark.parametrize("pair", _pairs(), ids=[f"pair{i}" for i in range(10)])
def test_the_ratio_is_the_fallbacks_own_number(pair):
    hint, title, cluster_id = pair
    reference = difflib.SequenceMatcher(
        None, hint.lower()[:100], (title or cluster_id or "").lower()[:100]
    ).ratio()
    assert svc.title_duplicate_ratio(hint, title or cluster_id) == reference
    assert svc.DUPLICATE_TITLE_RATIO == 0.7


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", _pairs(), ids=[f"pair{i}" for i in range(10)])
async def test_the_fallback_answers_byte_identically(pair, no_memory_no_chroma):
    hint, title, cluster_id = pair
    defects = [_defect("Unrelated: nightly export job", "cluster-zz"), _defect(title, cluster_id)]
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = defects
    db.execute.return_value = result

    answer = await svc._find_duplicate_semantic(project_id=PROJECT, duplicate_hint=hint, db=db)

    assert answer == _pre_w16_fallback(hint, defects)


def test_the_answers_cover_both_outcomes():
    """The ten pairs exercise a match AND a miss, so a constant answer fails."""
    outcomes = {svc.title_duplicate_ratio(h, t or c) > svc.DUPLICATE_TITLE_RATIO for h, t, c in _pairs()}
    assert outcomes == {True, False}
