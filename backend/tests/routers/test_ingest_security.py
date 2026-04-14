"""
Regression tests for ingest router security fixes:
  - OOM via unbounded file read (50MB cap enforced streaming)
  - Tenant isolation bypass (project_id check was advisory only)
  - Unvalidated project_id string
"""
import pytest

pytest.importorskip("fastapi")
# app.core.deps (imported transitively by app.routers.ingest) depends on
# python-jose and asyncpg. They are present in the Docker test image but
# may be absent from a bare local checkout, so we skip cleanly when they
# are — same pattern as other auth/DB-adjacent tests in the repo.
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from fastapi import HTTPException  # noqa: E402


# ── _read_upload_bounded ────────────────────────────────────────────────────


class _FakeUploadFile:
    """Minimal UploadFile stand-in with an async read(n) method."""

    def __init__(self, total_bytes: int, chunk_size: int = 1024 * 1024):
        self._remaining = total_bytes
        self._chunk = chunk_size

    async def read(self, n: int = -1) -> bytes:
        if self._remaining <= 0:
            return b""
        take = min(n if n > 0 else self._chunk, self._remaining)
        self._remaining -= take
        return b"\x00" * take


@pytest.mark.asyncio
async def test_read_upload_bounded_accepts_file_under_limit():
    from app.routers.ingest import _read_upload_bounded

    # 10 MB file, 50 MB limit — should return all 10 MB.
    f = _FakeUploadFile(total_bytes=10 * 1024 * 1024)
    content = await _read_upload_bounded(f, max_size=50 * 1024 * 1024)
    assert len(content) == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_read_upload_bounded_aborts_oversized_file_early():
    """
    Core regression: a 200 MB file with a 50 MB cap must raise 413 BEFORE
    the entire payload is in memory. We measure "early" by confirming the
    running-total abort fires once we exceed the cap, not after all bytes
    have been consumed.
    """
    from app.routers.ingest import _read_upload_bounded

    f = _FakeUploadFile(total_bytes=200 * 1024 * 1024, chunk_size=4 * 1024 * 1024)
    with pytest.raises(HTTPException) as exc_info:
        await _read_upload_bounded(f, max_size=50 * 1024 * 1024)
    assert exc_info.value.status_code == 413
    # At abort time, at most max_size + one chunk has been read into the
    # `chunks` buffer — the attacker cannot force the server to fully
    # materialize the 200 MB payload in memory.
    assert f._remaining > 0  # still bytes we never read


@pytest.mark.asyncio
async def test_read_upload_bounded_exact_limit_ok():
    """File exactly equal to the limit must be accepted, not rejected."""
    from app.routers.ingest import _read_upload_bounded

    f = _FakeUploadFile(total_bytes=50 * 1024 * 1024)
    content = await _read_upload_bounded(f, max_size=50 * 1024 * 1024)
    assert len(content) == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_read_upload_bounded_empty_file_ok():
    from app.routers.ingest import _read_upload_bounded

    f = _FakeUploadFile(total_bytes=0)
    content = await _read_upload_bounded(f, max_size=50 * 1024 * 1024)
    assert content == b""


# ── _parse_project_uuid ─────────────────────────────────────────────────────


def test_parse_project_uuid_valid():
    from app.routers.ingest import _parse_project_uuid
    import uuid

    u = uuid.uuid4()
    assert _parse_project_uuid(str(u)) == u


def test_parse_project_uuid_rejects_non_uuid():
    from app.routers.ingest import _parse_project_uuid

    with pytest.raises(HTTPException) as exc_info:
        _parse_project_uuid("not-a-uuid")
    assert exc_info.value.status_code == 400


def test_parse_project_uuid_rejects_literal_all():
    """The ALL_PROJECTS_ID sentinel string must not leak through as a UUID."""
    from app.routers.ingest import _parse_project_uuid

    with pytest.raises(HTTPException) as exc_info:
        _parse_project_uuid("all")
    assert exc_info.value.status_code == 400


def test_parse_project_uuid_rejects_empty():
    from app.routers.ingest import _parse_project_uuid

    with pytest.raises(HTTPException) as exc_info:
        _parse_project_uuid("")
    assert exc_info.value.status_code == 400
