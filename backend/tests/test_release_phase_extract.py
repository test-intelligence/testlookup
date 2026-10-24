"""W11 REL-07: reading the PRD -- exactly one of text or a file, safely.

* ``.md`` / ``.txt`` decode as UTF-8 (a BOM dropped, CRLF -> LF, NUL
  removed); invalid UTF-8 is a sentence, not a 500;
* anything but ``.md`` / ``.txt`` / ``.docx`` is refused by name;
* a ``.docx`` is a ZIP: its members are measured BEFORE python-docx opens it,
  so a zip bomb (a 10 MB member compressed far below 200 KB) is refused
  without python-docx ever being called; too many members, a broken archive
  and an over-long extracted text are refused too;
* text AND a file is refused; neither is refused;
* at the route, an upload past 200 KB is a 413 (read in chunks, so it is
  never resident whole), and a refusal is the sentence the form shows.
"""
from __future__ import annotations

import io
import uuid
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.core.release_planning import (
    MAX_PRD_BYTES,
    PRD_BAD_DOCX,
    PRD_BAD_TYPE,
    PRD_BOTH,
    PRD_EMPTY,
    PRD_NOT_UTF8,
    PRD_TOO_LARGE,
)
from app.models.postgres import Release, UserRole
from app.routers import releases as rel_router
from app.services.release_phase_generator import PrdInputError, extract_prd_text


def _refused(**kwargs) -> str:
    with pytest.raises(PrdInputError) as exc:
        extract_prd_text(**kwargs)
    return str(exc.value)


def test_markdown_with_a_bom_and_crlf_reads_clean():
    data = "﻿## Smoke\r\nRun it.\r\n\x00## UAT\r\n".encode("utf-8")
    assert extract_prd_text(text=None, filename="PRD.MD", data=data) == "## Smoke\nRun it.\n## UAT\n"


def test_pasted_text_is_normalised_the_same_way():
    assert extract_prd_text(text="## Smoke\r\n\r\n## UAT", filename=None, data=None) == "## Smoke\n\n## UAT"


def test_invalid_utf8_is_refused_by_name():
    assert _refused(text=None, filename="notes.txt", data=b"## Smoke\n\xff\xfe\xfa") == PRD_NOT_UTF8


@pytest.mark.parametrize("name", ["prd.pdf", "prd.docm", "prd", "prd.md.exe", None])
def test_other_file_types_are_refused(name):
    assert _refused(text=None, filename=name, data=b"## Smoke") == PRD_BAD_TYPE


def test_text_and_a_file_together_are_refused():
    assert _refused(text="## Smoke", filename="prd.md", data=b"## UAT") == PRD_BOTH


@pytest.mark.parametrize("kwargs", [
    {"text": None, "filename": None, "data": None},
    {"text": "   \n ", "filename": None, "data": None},
    {"text": None, "filename": "empty.md", "data": b"\n\n  \n"},
])
def test_nothing_to_read_is_refused(kwargs):
    assert _refused(**kwargs) == PRD_EMPTY


def test_text_past_200_kb_is_refused():
    assert _refused(text="a" * (MAX_PRD_BYTES + 1), filename=None, data=None) == PRD_TOO_LARGE


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


@pytest.fixture
def docx_spy(monkeypatch):
    docx = pytest.importorskip("docx")
    calls: list[int] = []
    real = docx.Document

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(docx, "Document", spy)
    return calls


def test_a_zip_bomb_is_refused_before_python_docx_opens_it(docx_spy):
    bomb = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"\0" * (10 * 1024 * 1024)})
    assert len(bomb) < MAX_PRD_BYTES   # small on the wire, 10 MB unpacked
    assert _refused(text=None, filename="payments-prd.docx", data=bomb) == PRD_BAD_DOCX
    assert docx_spy == []


def test_too_many_members_are_refused_before_python_docx_opens_it(docx_spy):
    archive = _zip({f"word/part{i}.xml": b"x" for i in range(301)})
    assert _refused(text=None, filename="prd.docx", data=archive) == PRD_BAD_DOCX
    assert docx_spy == []


def test_a_broken_archive_is_refused(docx_spy):
    assert _refused(text=None, filename="prd.docx", data=b"PK\x03\x04 not really a zip") == PRD_BAD_DOCX
    assert docx_spy == []


def test_a_zip_that_is_not_a_document_is_refused_after_the_size_check(docx_spy):
    assert _refused(text=None, filename="prd.docx", data=_zip({"hello.txt": b"hi"})) == PRD_BAD_DOCX
    assert docx_spy == [1]


def test_a_docx_whose_text_unpacks_past_the_limit_is_refused():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("a" * 200_001)
    buffer = io.BytesIO()
    document.save(buffer)
    assert len(buffer.getvalue()) < MAX_PRD_BYTES
    assert _refused(text=None, filename="long.docx", data=buffer.getvalue()) == PRD_TOO_LARGE


# ── At the route ─────────────────────────────────────────────────────────────


class _Session:
    def __init__(self, release):
        self.release = release
        self.statements = 0
        self.new, self.dirty, self.deleted = [], [], []

    async def execute(self, *_a, **_k):
        self.statements += 1
        return SimpleNamespace(scalar_one_or_none=lambda: self.release)

    async def close(self):
        return None


def _user():
    return SimpleNamespace(id=uuid.uuid4(), role=UserRole.QA_LEAD)


async def _draft(*, text=None, file=None):
    release = Release(id=uuid.uuid4(), project_id=uuid.uuid4(), name="2026.13")
    return await rel_router.draft_release_phases(
        release_id=str(release.id), text=text, file=file, db=_Session(release),
        current_user=_user(), __=_user(),
    )


async def test_an_upload_past_200_kb_is_a_413_at_the_route():
    upload = UploadFile(file=io.BytesIO(b"a" * (MAX_PRD_BYTES + 1)), filename="prd.md")
    with pytest.raises(HTTPException) as exc:
        await _draft(file=upload)
    assert (exc.value.status_code, exc.value.detail) == (413, PRD_TOO_LARGE)


async def test_an_upload_of_exactly_200_kb_is_read(monkeypatch):
    upload = UploadFile(file=io.BytesIO(b"## Smoke\n" + b"a" * (MAX_PRD_BYTES - 9)), filename="prd.md")

    async def config(*_a, **_k):
        return {"analysis_mode": "rules"}   # rules mode keeps the model out of this test

    monkeypatch.setattr(rel_router, "get_effective_ai_config", config)
    out = await _draft(file=upload)
    assert out["source"] == "rules" and out["fallback_reason"] == "rules_mode"
    assert out["text_chars"] == MAX_PRD_BYTES
    assert [p["name"] for p in out["phases"]] == ["Smoke"]


async def test_pasted_text_past_200_kb_is_a_413_too():
    with pytest.raises(HTTPException) as exc:
        await _draft(text="a" * (MAX_PRD_BYTES + 1))
    assert (exc.value.status_code, exc.value.detail) == (413, PRD_TOO_LARGE)


async def test_a_refusal_is_a_422_with_the_sentence():
    with pytest.raises(HTTPException) as exc:
        await _draft(file=UploadFile(file=io.BytesIO(b"%PDF-1.7"), filename="prd.pdf"))
    assert (exc.value.status_code, exc.value.detail) == (422, PRD_BAD_TYPE)
    with pytest.raises(HTTPException) as exc:
        await _draft(text="## Smoke", file=UploadFile(file=io.BytesIO(b"## UAT"), filename="prd.md"))
    assert (exc.value.status_code, exc.value.detail) == (422, PRD_BOTH)
    with pytest.raises(HTTPException) as exc:
        await _draft()
    assert (exc.value.status_code, exc.value.detail) == (422, PRD_EMPTY)
