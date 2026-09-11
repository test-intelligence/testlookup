"""Real PostgreSQL: a production engine's failed INSERT carries no bound value.

Re-audit H3, code review of the Q2 fix. ``tests/regression/
test_engine_hides_parameters.py`` pins the engine flag; this proves what the
flag is for, against a real server. The error text of a failed INSERT, and the
traceback the logging pipeline writes for it, must not contain the value the
statement bound -- here a bcrypt-shaped password hash, which no redaction
pattern can recognise, since it carries no marker.
"""
from __future__ import annotations

import io
import logging
import os

import pytest

pytest.importorskip("asyncpg")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set TEST_DATABASE_URL or TESTLOOKUP_POSTGRES_TEST_DSN",
    ),
]

_PASSWORD_HASH = "$2b$12$R9h/cIPz0gi.URNNX3kh2OPST9/PgBkqquzi.Ss7KIUgO2t0jWMUW"


@pytest.fixture
async def engine_under(monkeypatch):
    """``get_engine()`` itself, pointed at the test server, under an APP_ENV."""
    from app.core.config import settings
    from app.db import postgres

    built = []

    def build(app_env: str):
        monkeypatch.setattr(settings, "APP_ENV", app_env)
        monkeypatch.setattr(settings, "DATABASE_URL", DATABASE_URL)
        postgres.get_session_factory.cache_clear()
        postgres.get_engine.cache_clear()
        engine = postgres.get_engine()
        built.append(engine)
        return engine

    try:
        yield build
    finally:
        for engine in built:
            await engine.dispose()
        postgres.get_session_factory.cache_clear()
        postgres.get_engine.cache_clear()


@pytest.fixture
def json_logs(monkeypatch):
    """The real pipeline from configure_logging(), writing JSON to a buffer."""
    import structlog

    from app.core import logging_config
    from app.core.config import settings

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_structlog = structlog.get_config()
    monkeypatch.setattr(settings, "LOG_FORMAT", "json")
    logging_config.configure_logging()
    buffer = io.StringIO()
    root.handlers[-1].setStream(buffer)
    try:
        yield buffer
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        structlog.configure(**saved_structlog)


async def _insert_twice(engine, password_hash: str) -> None:
    """Insert one primary key twice; the second, failing INSERT binds the hash.

    The temporary table lives in the transaction the failure rolls back, so
    nothing is left behind on the server or in the pool's session.
    """
    async with engine.connect() as connection:
        await connection.execute(
            text(
                "CREATE TEMP TABLE q2_accounts "
                "(id integer PRIMARY KEY, password_hash text NOT NULL)"
            )
        )
        insert = text(
            "INSERT INTO q2_accounts (id, password_hash) VALUES (:id, :password_hash)"
        )
        await connection.execute(insert, {"id": 1, "password_hash": "first-row"})
        await connection.execute(insert, {"id": 1, "password_hash": password_hash})


async def test_a_production_engine_keeps_the_bound_value_out_of_the_error(engine_under):
    with pytest.raises(IntegrityError) as caught:
        await _insert_twice(engine_under("production"), _PASSWORD_HASH)

    rendered = str(caught.value)
    assert "INSERT INTO q2_accounts" in rendered, "the statement itself is still reported"
    assert "hide_parameters=True" in rendered
    assert _PASSWORD_HASH not in rendered


async def test_outside_production_the_value_is_still_there_to_debug(engine_under):
    """The control: with the flag off the same failure carries the value, so
    the production test above can see a leak when there is one."""
    with pytest.raises(IntegrityError) as caught:
        await _insert_twice(engine_under("development"), _PASSWORD_HASH)

    assert _PASSWORD_HASH in str(caught.value)


async def test_the_logged_traceback_of_a_production_failure_has_no_value(
    engine_under, json_logs
):
    import structlog

    try:
        await _insert_twice(engine_under("production"), _PASSWORD_HASH)
    except IntegrityError:
        structlog.get_logger("tests.q2").exception("account insert failed")
    else:
        pytest.fail("the duplicate INSERT did not fail")

    written = json_logs.getvalue()
    assert "IntegrityError" in written, "the traceback was not logged"
    assert "q2_accounts" in written
    assert _PASSWORD_HASH not in written
