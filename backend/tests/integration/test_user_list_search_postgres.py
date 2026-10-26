"""ADM-03: the user list pages with a total and searches server-side.

``GET /api/v1/users`` returned at most 200 users with no total and no search,
so the Users page showed a truncation notice and the Members picker (which
asked for the default 50) silently could not find the rest. The body stays a
bare list; ``q`` matches the username, email or full name case-insensitively
(``%`` and ``_`` are literal), and ``X-Total-Count`` is the filtered count
across every page.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head,
skips otherwise. Nothing is committed: the session is rolled back. Runs in CI
as part of the ``postgres-integration`` job's file list.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def session():
    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        try:
            yield db
        finally:
            await db.rollback()
    await engine.dispose()


@pytest.fixture
async def token(session):
    """Sixty users sharing a non-hex marker, plus a few with distinctive names."""
    from app.models.postgres import User, UserRole

    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")  # never a substring of another row's uuid-built name

    def user(name: str, *, full_name: str, role=UserRole.QA_ENGINEER, active=True) -> User:
        return User(
            id=uuid.uuid4(), email=f"{name}-{marker}@example.test", username=f"{name}-{marker}",
            full_name=full_name, hashed_password="x", role=role, is_active=active,
        )

    rows = [user(f"bulk{i:02d}", full_name=f"Bulk {i:02d} {marker}") for i in range(60)]
    rows += [
        user("eng_sara", full_name=f"Sara Engineer {marker}"),
        user("lead_pct", full_name=f"Percent 100% {marker}", role=UserRole.QA_LEAD),
        user("under_score", full_name=f"Under Score {marker}", active=False),
    ]
    session.add_all(rows)
    await session.flush()
    return marker


async def _list(session, **kw):
    from app.models.postgres import User, UserRole
    from app.routers.users import list_users

    response = Response()
    caller = User(id=uuid.uuid4(), username="caller", email="c@example.test", role=UserRole.ADMIN)
    rows = await list_users(
        response=response, is_active=kw.pop("is_active", None), role=kw.pop("role", None),
        q=kw.pop("q", None), page=kw.pop("page", 1), page_size=kw.pop("page_size", 50),
        db=session, current_user=caller,
    )
    return [u.username for u in rows], int(response.headers["X-Total-Count"])


async def test_the_total_counts_every_page_and_the_pages_do_not_overlap(session, token):
    first, total = await _list(session, q=token, page=1, page_size=50)
    second, total2 = await _list(session, q=token, page=2, page_size=50)
    assert total == total2 == 63
    assert len(first) == 50 and len(second) == 13
    assert not set(first) & set(second)


async def test_q_matches_username_email_or_full_name_case_insensitively(session, token):
    by_username, n = await _list(session, q=f"ENG_SARA-{token}")
    assert by_username == [f"eng_sara-{token}"] and n == 1
    by_email, _ = await _list(session, q=f"sara-{token}@EXAMPLE")
    assert by_email == [f"eng_sara-{token}"]
    by_name, _ = await _list(session, q=f"sara engineer {token}")
    assert by_name == [f"eng_sara-{token}"]


async def test_percent_and_underscore_are_literal(session, token):
    pct, n = await _list(session, q=f"100% {token}")
    assert pct == [f"lead_pct-{token}"] and n == 1
    # "_" is not a one-character wildcard: "eng_sara" does not match "engxsara"-like rows, and
    # "under_score" matches only itself
    under, n2 = await _list(session, q=f"under_score-{token}")
    assert under == [f"under_score-{token}"] and n2 == 1
    none, n3 = await _list(session, q=f"eng%sara-{token}")
    assert none == [] and n3 == 0


async def test_role_and_active_filters_combine_with_q_and_the_total(session, token):
    from app.models.postgres import UserRole

    leads, n = await _list(session, q=token, role=UserRole.QA_LEAD)
    assert leads == [f"lead_pct-{token}"] and n == 1
    inactive, n2 = await _list(session, q=token, is_active=False)
    assert inactive == [f"under_score-{token}"] and n2 == 1
    active, n3 = await _list(session, q=token, is_active=True, page_size=200)
    assert n3 == 62 and len(active) == 62


async def test_a_blank_q_is_no_filter(session, token):
    _, everyone = await _list(session, q="   ")
    _, unfiltered = await _list(session)
    assert everyone == unfiltered >= 63
