"""A missing bucket means zero objects, not a 500.

Found while exercising retention on the live deployment. ``POST
/projects/{id}/retention-policy/preview`` returned **HTTP 500** the moment the
project had anything to purge:

    botocore.errorfactory.NoSuchBucket: An error occurred (NoSuchBucket) when
    calling the ListObjectsV2 operation: The specified bucket does not exist

It worked while the answer was zero, because with no runs past the cutoff there
are no artifact prefixes to list — so the bug hid until the feature had
something to say.

Two consequences, and the second is the serious one.

**Preview is read-only and still crashed.** It is the operation admins are told
to run before enabling retention: "preview is how admins decide whether to
enable" (``run_purge`` docstring). On any deployment where nothing has uploaded
an artifact yet — the default state — that decision could not be made.

**Execute would abort mid-purge.** The documented order is Mongo → MinIO →
Postgres. Mongo deletes happen at step (2); the MinIO listing that raises is at
step (3). So a purge would delete Mongo documents, throw, and never reach the
Postgres deletes or the audit row — a partial purge with no record of itself,
on a code path whose own docstring notes the stores are "inherently
non-transactional".

An absent bucket is not an error state for a *read*: it contains no objects, so
the honest answers are ``[]`` and ``0``. Both list and delete paths now say so.
Anything other than ``NoSuchBucket`` still raises — a permissions or network
failure must not be silently reported as "nothing there".
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("botocore")

from app.db.storage import S3StorageProvider  # noqa: E402

pytestmark = pytest.mark.regression


def _no_such_bucket():
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "The specified bucket does not exist"}},
        "ListObjectsV2",
    )


def _other_error():
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "ListObjectsV2"
    )


class _Paginator:
    def __init__(self, exc):
        self._exc = exc

    def paginate(self, **_kw):
        async def _gen():
            raise self._exc
            yield  # pragma: no cover — makes this an async generator

        return _gen()


class _Client:
    def __init__(self, exc):
        self._exc = exc
        self.delete_objects_calls = 0

    def get_paginator(self, _name):
        return _Paginator(self._exc)

    async def delete_objects(self, **_kw):  # pragma: no cover — must not run
        self.delete_objects_calls += 1


class _Ctx:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *_a):
        return False


def _provider(exc):
    with patch("app.db.storage.S3StorageProvider.__init__", lambda self: None):
        p = S3StorageProvider()
    client = _Client(exc)
    p.get_client_context = MagicMock(return_value=_Ctx(client))  # type: ignore[method-assign]
    return p, client


class TestListObjects:
    @pytest.mark.asyncio
    async def test_missing_bucket_lists_nothing(self):
        provider, _ = _provider(_no_such_bucket())
        assert await provider.list_objects("runs/abc/", bucket="nope") == [], (
            "a missing bucket raised instead of reporting zero objects — this "
            "500s the read-only retention preview"
        )

    @pytest.mark.asyncio
    async def test_other_errors_still_raise(self):
        """AccessDenied must not be reported as an empty bucket."""
        provider, _ = _provider(_other_error())
        from botocore.exceptions import ClientError

        with pytest.raises(ClientError):
            await provider.list_objects("runs/abc/", bucket="nope")


class TestDeletePrefix:
    @pytest.mark.asyncio
    async def test_missing_bucket_deletes_nothing(self):
        """Execute must not abort here — Mongo deletes have already happened."""
        provider, client = _provider(_no_such_bucket())
        assert await provider.delete_prefix("runs/abc/", bucket="nope") == 0
        assert client.delete_objects_calls == 0

    @pytest.mark.asyncio
    async def test_other_errors_still_raise(self):
        provider, _ = _provider(_other_error())
        from botocore.exceptions import ClientError

        with pytest.raises(ClientError):
            await provider.delete_prefix("runs/abc/", bucket="nope")

    @pytest.mark.asyncio
    async def test_the_empty_prefix_guard_still_fires_first(self):
        """A bucket-wiping prefix must be refused before anything else."""
        provider, _ = _provider(_no_such_bucket())
        with pytest.raises(ValueError):
            await provider.delete_prefix("", bucket="nope")
