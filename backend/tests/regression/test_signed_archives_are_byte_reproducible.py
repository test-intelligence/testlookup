"""Regression — a signed archive is byte-reproducible: ``signature.json`` carries the fixed ZIP timestamp.

``test_project_export_people::test_a_catalog_only_export_stays_schema_1_byte_for_byte_and_carries_no_email``
failed once on PR #375's CI (run 43053849052, ``[options1]``, first differing byte 5788) and passed on rerun.
``_sign_pack`` appended ``signature.json`` with ``zf.writestr(<str name>, ...)``, and ``zipfile`` stamps a
str-named member with the local wall clock (a DOS time, 2-second resolution). Every other member of a
project / run / subject / retention archive goes through ``_member_info`` and its fixed 1980-01-01 timestamp,
so two exports of the same data either side of a 2-second tick differed only in ``signature.json``'s local
and central headers (same CRC, same content) -- 3-6 pairs in 600 locally.

Pinned: the same archive signed twice, with ``zipfile``'s clock moved by more than 2 s between the two
signs, is the same bytes -- in memory (a compliance pack's ``bytes``) and appended in place to a file object
(a spooled export) -- and every member, ``signature.json`` included, carries ``_FIXED_ZIP_DATE_TIME``.
"""
from __future__ import annotations

import io
import time
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services import compliance_pack_service as svc
from app.services import compliance_signing as signing

PACK_ID = uuid.UUID("5e1f0000-0000-4000-8000-00000000c0de")
GENERATED_AT = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
SIGNED_AT = datetime(2026, 11, 11, 12, 0, 1, tzinfo=timezone.utc)
#: Two wall clocks 3 s apart: always a different DOS time (2-second resolution).
FIRST_SIGN = time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))
SECOND_SIGN = FIRST_SIGN + 3


class _Clock:
    """``zipfile``'s ``time`` module with a settable ``time()``; every other name is the real module's."""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    # Python 3.14's zipfile prefers SOURCE_DATE_EPOCH over the clock; this test needs the clock.
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    fake = _Clock(FIRST_SIGN)
    monkeypatch.setattr(zipfile, "time", fake)
    return fake


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> signing.SigningKey:
    private = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)   # Ed25519 signs deterministically
    public = private.public_key()
    fixed = signing.SigningKey(
        key_id=signing.key_id_for(public), public_key_pem=signing.public_key_pem(public), private_key=private,
    )

    async def active(db: Any, *, actor: Any, create: bool) -> signing.SigningKey:
        return fixed

    monkeypatch.setattr(signing, "active_signing_key", active)
    return fixed


async def _members():
    yield "settings/project.json", b'{"name": "Reproducible"}\n'
    yield "catalog/test_cases.jsonl", b'{"id": 1}\n{"id": 2}\n' * 200
    yield "README.txt", b"An export.\n"


async def _unsigned() -> tuple[bytes, str]:
    scope = svc.ExportScope.for_project(project_id=uuid.UUID(int=1), manifest_extra={"format": "regression"})
    sink = io.BytesIO()
    result = await svc.build_archive(scope, members=_members(), sink=sink, generated_at=GENERATED_AT)
    return sink.getvalue(), result.manifest_sha256


async def _sign(archive: bytes, manifest_sha256: str, *, as_file: bool) -> bytes:
    signed, signature, _key_id, snapshot = await svc._sign_pack(
        None, io.BytesIO(archive) if as_file else archive,   # type: ignore[arg-type] - the key lookup is faked
        pack_id=PACK_ID, manifest_sha256=manifest_sha256, actor=None, signed_at=SIGNED_AT,
    )
    assert signature and snapshot["state"] == "signed"
    if as_file:
        assert isinstance(signed, io.BytesIO), "a file object is appended in place"
        return signed.getvalue()
    assert isinstance(signed, bytes)
    return signed


def _str_name_stamp() -> tuple[int, ...]:
    """What ``zipfile`` stamps on a str-named member under the current (fake) clock."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("probe.txt", b"x")
    return zipfile.ZipFile(buffer).getinfo("probe.txt").date_time


@pytest.mark.parametrize("as_file", [False, True], ids=["bytes", "file_object"])
async def test_signing_the_same_archive_either_side_of_a_dos_time_tick_gives_the_same_bytes(
    clock: _Clock, as_file: bool,
) -> None:
    unsigned, manifest_sha256 = await _unsigned()

    clock.now = FIRST_SIGN
    first_stamp = _str_name_stamp()
    first = await _sign(unsigned, manifest_sha256, as_file=as_file)

    clock.now = SECOND_SIGN
    assert _str_name_stamp() != first_stamp, "the clock this test moves must be the one zipfile reads"
    second = await _sign(unsigned, manifest_sha256, as_file=as_file)

    assert first == second, next(
        (f"the two signs differ at byte {i}" for i, (a, b) in enumerate(zip(first, second)) if a != b),
        "the two signs differ in length",
    )
    with zipfile.ZipFile(io.BytesIO(second)) as zf:
        stamps = {info.filename: info.date_time for info in zf.infolist()}
        assert zf.getinfo(signing.SIGNATURE_FILENAME).compress_type == zipfile.ZIP_DEFLATED
    assert list(stamps)[-1] == signing.SIGNATURE_FILENAME
    assert stamps == dict.fromkeys(stamps, svc._FIXED_ZIP_DATE_TIME)
