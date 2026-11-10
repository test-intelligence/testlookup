"""A promoted model name reads back from the shared Redis client, which decodes responses (W47).

``get_redis()`` builds its pool with ``decode_responses=True``, so ``GET`` returns ``str``. ModelRegistry called
``value.decode()`` on it, which raises ``AttributeError`` the moment any model is promoted: llm_factory swallowed the
error (a promoted fine-tuned model was silently never used) and analysis_router raised it. redis-py 8's typing made
mypy report it. The client here returns ``str`` exactly like the real decoded client.
"""
from __future__ import annotations

import json

from app.services import model_registry
from app.services.model_registry import ModelRegistry


class DecodedRedis:
    """What ``get_redis()`` returns for these calls: decoded ``str`` values."""

    def __init__(self, values: dict[str, str]):
        self.values = values

    async def get(self, key: str):
        return self.values.get(key)


async def test_the_active_model_reads_back_as_text(monkeypatch):
    client = DecodedRedis({"testlookup:model:active:classifier": "ft-classifier-v2"})
    monkeypatch.setattr(model_registry, "get_redis", lambda: client)

    assert await ModelRegistry.get_active_model("classifier") == "ft-classifier-v2"
    assert await ModelRegistry.get_active_model("reasoning") is None


async def test_the_status_lists_the_promoted_model(monkeypatch):
    client = DecodedRedis({
        "testlookup:model:active:classifier": "ft-classifier-v2",
        "testlookup:model:metrics:classifier": json.dumps({"accuracy": 0.91}),
    })
    monkeypatch.setattr(model_registry, "get_redis", lambda: client)

    status = await ModelRegistry.get_all_status()

    assert status["classifier"]["active_model"] == "ft-classifier-v2"
    assert status["classifier"]["metrics"] == {"accuracy": 0.91}
    assert status["embedding"]["active_model"] is None


def test_bytes_from_an_undecoded_client_still_decode():
    assert model_registry._text(b"ft-classifier-v2") == "ft-classifier-v2"
    assert model_registry._text("ft-classifier-v2") == "ft-classifier-v2"
