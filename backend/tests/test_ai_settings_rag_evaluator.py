"""The RAG faithfulness evaluator is set and read through ``/settings/ai``.

``get_effective_ai_config`` publishes ``rag_faithfulness_evaluator`` from the
stored ``ai_config`` row and the gate honours ``"ragas"`` (PR #259), but
nothing could write the key: ``AIConfigUpdate`` had no such field, so Pydantic
dropped it from ``PUT /settings/ai``. Worse, the PUT rewrites the whole row
from ``_load_ai_config``, which did not carry the key either, so saving any
AI setting deleted an evaluator stored by hand.

Pinned here, through the HTTP app and the real resolver:

* a PUT stores the choice, GET returns it, and ``_resolve_backend()`` runs it;
* an unknown strategy is a 422 and nothing is written;
* saving another field keeps the stored choice;
* the page reports the strategy that runs: offline mode (env or stored) and a
  missing ``ragas`` package both mean the single-prompt judge, and the stored
  choice is kept so it applies once the cause is gone.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("asyncpg")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.deps import get_current_active_user  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.models.postgres import User, UserRole  # noqa: E402
from app.routers import app_settings as router_mod  # noqa: E402
from app.services import rag_faithfulness_service as svc  # noqa: E402

URL = "/api/v1/settings/ai"


class _Db:
    """One ``ai_config`` row, served to the settings router and the resolver."""

    def __init__(self, value: dict | None = None):
        self.row = SimpleNamespace(value=value) if value is not None else None
        self.commits = 0

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    def add(self, obj):
        self.row = obj

    async def commit(self):
        self.commits += 1


class _SessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def stack(monkeypatch, fake_redis):
    """The settings router over a fake row, and the real resolver over the same row."""
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake_redis)
    monkeypatch.setattr(svc, "ragas_installed", lambda: True)

    async def _no_secret(_db, _scope, _key):
        return None

    monkeypatch.setattr("app.services.secret_service.read_secret", _no_secret)
    monkeypatch.setattr(
        "app.services.agent_authority_lock.lock_global_agent_authority", AsyncMock()
    )
    monkeypatch.setattr(router_mod, "log_settings_change", AsyncMock())
    monkeypatch.setattr(router_mod, "_ml_status", AsyncMock(return_value={}))

    flags = {"knowledge_rag": False, "rag_faithfulness_gate": True}
    flag_calls: list[tuple[str, bool]] = []

    async def _is_enabled(key, *, db=None, any_scope=False, **_kw):
        flag_calls.append((key, any_scope))
        return flags.get(key, False)

    monkeypatch.setattr("app.services.feature_flags.is_enabled", _is_enabled)

    db = _Db()
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal", lambda: _SessionCtx(db)
    )

    app = FastAPI()
    app.include_router(router_mod.router)

    async def _get_db():
        yield db

    admin = User(
        id=uuid.uuid4(), username="admin", email="admin@example.test",
        role=UserRole.ADMIN, is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    client = AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )
    return SimpleNamespace(client=client, db=db, flags=flags, flag_calls=flag_calls)


@pytest.mark.asyncio
async def test_a_put_of_ragas_is_stored_returned_and_run(stack):
    # Prime the resolver's cache with the default first: the PUT must
    # invalidate it, or the gate keeps judging with the old strategy.
    assert await svc._resolve_backend() == "ollama"

    async with stack.client as http:
        put = await http.put(URL, json={"rag_faithfulness_evaluator": "ragas"})
        assert put.status_code == 200, put.text
        assert put.json()["rag_faithfulness_evaluator"] == "ragas"
        assert put.json()["rag_faithfulness_evaluator_effective"] == "ragas"
        assert put.json()["rag_faithfulness_evaluator_reason"] == "configured"

        assert stack.db.row.value["rag_faithfulness_evaluator"] == "ragas"
        assert await svc._resolve_backend() == "ragas"

        got = (await http.get(URL)).json()
    assert got["rag_faithfulness_evaluator"] == "ragas"
    assert got["rag_faithfulness_evaluator_effective"] == "ragas"


@pytest.mark.asyncio
async def test_an_unknown_strategy_is_a_422_and_nothing_is_written(stack):
    stack.db.row = SimpleNamespace(value={"rag_faithfulness_evaluator": "ragas"})

    async with stack.client as http:
        resp = await http.put(URL, json={"rag_faithfulness_evaluator": "gpt-judge"})

    assert resp.status_code == 422, resp.text
    assert stack.db.commits == 0
    assert stack.db.row.value == {"rag_faithfulness_evaluator": "ragas"}


@pytest.mark.asyncio
async def test_saving_another_field_keeps_the_stored_choice(stack):
    stack.db.row = SimpleNamespace(value={"rag_faithfulness_evaluator": "ragas"})

    async with stack.client as http:
        resp = await http.put(URL, json={"llm_model": "qwen2.5:14b"})

    assert resp.status_code == 200, resp.text
    assert stack.db.row.value["rag_faithfulness_evaluator"] == "ragas"
    assert stack.db.row.value["llm_model"] == "qwen2.5:14b"
    # What runs is derived on every read; a stored copy would outlive its cause.
    assert "rag_faithfulness_evaluator_effective" not in stack.db.row.value
    assert "rag_faithfulness_evaluator_reason" not in stack.db.row.value


@pytest.mark.asyncio
@pytest.mark.parametrize("env_offline, stored", [(True, {}), (False, {"ai_offline_mode": True})])
async def test_offline_mode_reports_the_judge_that_runs(stack, monkeypatch, env_offline, stored):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", env_offline)
    stack.db.row = SimpleNamespace(value={**stored, "rag_faithfulness_evaluator": "ragas"})

    async with stack.client as http:
        got = (await http.get(URL)).json()

    # The choice is kept (it applies once offline mode is off); the page says
    # it is not what runs, and why.
    assert got["rag_faithfulness_evaluator"] == "ragas"
    assert got["rag_faithfulness_evaluator_effective"] == "ollama"
    assert got["rag_faithfulness_evaluator_reason"] == "offline_mode"
    assert await svc._resolve_backend() == "ollama"


@pytest.mark.asyncio
async def test_without_the_ragas_package_the_page_and_the_label_say_so(stack, monkeypatch):
    monkeypatch.setattr(svc, "ragas_installed", lambda: False)
    stack.db.row = SimpleNamespace(
        value={"llm_provider": "openrouter", "rag_faithfulness_evaluator": "ragas"}
    )

    async with stack.client as http:
        got = (await http.get(URL)).json()

    assert got["rag_faithfulness_evaluator"] == "ragas"
    assert got["rag_faithfulness_evaluator_effective"] == "ollama"
    assert got["rag_faithfulness_evaluator_reason"] == "ragas_not_installed"

    # The gate agrees, and the judged case is labelled with the model that
    # judged it: it used to read "ragas" over a single-prompt score.
    monkeypatch.setattr(svc, "_feature_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(svc, "_evaluate_via_ollama", AsyncMock(return_value=(0.9, "supported")))
    result = await svc.evaluate("Login rejects a wrong password", ["Spec 4.2: 401"])
    assert result is not None
    assert result["evaluator"] == "openrouter"


@pytest.mark.asyncio
async def test_the_page_reports_the_gate_flag_deployment_wide(stack):
    async with stack.client as http:
        on = (await http.get(URL)).json()
        stack.flags["rag_faithfulness_gate"] = False
        off = (await http.get(URL)).json()

    assert on["rag_faithfulness_gate_enabled"] is True
    assert off["rag_faithfulness_gate_enabled"] is False
    # Any scope: a project allow-list still makes the choice matter.
    assert ("rag_faithfulness_gate", True) in stack.flag_calls


@pytest.mark.parametrize(
    "configured, offline, installed, expected",
    [
        (None, False, True, ("ollama", "configured")),
        ("ollama", True, True, ("ollama", "configured")),
        ("ragas", False, True, ("ragas", "configured")),
        (" RAGAS ", False, True, ("ragas", "configured")),
        ("ragas", True, True, ("ollama", "offline_mode")),
        ("ragas", False, False, ("ollama", "ragas_not_installed")),
        ("gpt-judge", False, True, ("ollama", "configured")),
    ],
)
def test_effective_evaluator(monkeypatch, configured, offline, installed, expected):
    monkeypatch.setattr(svc, "ragas_installed", lambda: installed)

    assert svc.effective_evaluator(configured, offline=offline) == expected
