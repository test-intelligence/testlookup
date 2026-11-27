"""Small async read-only pipeline fake for queue observation tests."""
from __future__ import annotations


class PipelineRedis:
    def pipeline(self, *, transaction=False):
        assert transaction is False
        return _QueuePipeline(self)


class _QueuePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.keys = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def llen(self, key):
        self.keys.append(key)
        return self

    async def execute(self, *, raise_on_error=True):
        values = []
        for key in self.keys:
            try:
                values.append(await self.redis.llen(key))
            except Exception as exc:
                if raise_on_error:
                    raise
                values.append(exc)
        return values
