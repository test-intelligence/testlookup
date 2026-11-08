"""W37 F-2 (contract §1 People) — erasing a person takes down every live project export that lists them, now that
an export may carry members, suite owners and e-mail addresses (``subject_erasure_service`` E2).

The archives are built the way an export that ticked Include members writes its directory; the session and the
store are recording doubles.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from typing import Any

import pytest

from app.services import subject_erasure_service as svc
from app.services.subject_data_inventory import INVENTORY


def _zip(members: dict[str, list[dict[str, Any]]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, lines in members.items():
            archive.writestr(name, "".join(json.dumps(line) + "\n" for line in lines))
        archive.writestr("manifest.json", json.dumps({"schema_version": 3}))
    return buffer.getvalue()


def _members_export(member: uuid.UUID, other: uuid.UUID, email: str = "erin@people.example.test") -> bytes:
    return _zip({
        "directory/users.jsonl": [
            {"id": str(member), "username": "erin", "full_name": "Erin E", "email": email},
            {"id": str(other), "username": "olaf", "full_name": None},
        ],
        "directory/members.jsonl": [{"user_id": str(member), "role": "QA_ENGINEER"}],
        "directory/suite_owners.jsonl": [{"suite_name": "Checkout", "user_id": str(member)}],
    })


def _lists(blob: bytes, user_id: uuid.UUID) -> bool:
    return svc._directory_lists(io.BytesIO(blob), user_id)


def test_the_scan_finds_a_person_in_any_people_list_of_an_export() -> None:
    member, other, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert _lists(_members_export(member, other), member) and _lists(_members_export(member, other), other)
    assert not _lists(_members_export(member, other), stranger)
    # defence in depth: a person listed ONLY as a member / suite owner (no users.jsonl line) is still found
    assert _lists(_zip({"directory/members.jsonl": [{"user_id": str(stranger), "role": "VIEWER"}]}), stranger)
    assert _lists(_zip({"directory/suite_owners.jsonl": [{"suite_name": "A", "user_id": str(stranger)}]}), stranger)
    assert not _lists(_zip({"directory/members.jsonl": [{"user_id": str(member), "role": "VIEWER"}]}), stranger)


class _Result:
    def __init__(self, rows: list[tuple] = (), rowcount: int = 0) -> None:
        self._rows, self.rowcount = list(rows), rowcount

    def all(self) -> list[tuple]:
        return self._rows


class _Session:
    """Answers the live-exports SELECT; the guarded UPDATE matches one row."""

    def __init__(self, live: list[tuple[uuid.UUID, str]]) -> None:
        self.live, self.updates = live, []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Result:
        if str(statement).lstrip().upper().startswith("SELECT"):
            return _Result(self.live)
        self.updates.append(statement.compile().params)
        return _Result(rowcount=1)


class _Store:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects, self.deleted = dict(objects), []

    async def stream_object(self, key: str, bucket: Any = None) -> Any:
        if key not in self.objects:
            raise FileNotFoundError(key)
        yield self.objects[key]

    async def delete_object(self, key: str, bucket: Any = None) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.mark.parametrize("who", ["member", "owner_only"])
def test_erasing_a_member_removes_the_export_with_their_email_and_member_line(who: str) -> None:
    member, other, bystander = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    listing = _members_export(member, other) if who == "member" else _zip(
        {"directory/users.jsonl": [], "directory/suite_owners.jsonl": [{"suite_name": "A", "user_id": str(member)}]})
    clean = _members_export(bystander, other, email="bea@people.example.test")
    store = _Store({"project-exports/a.zip": listing, "project-exports/b.zip": clean})
    jobs = [(uuid.uuid4(), "project-exports/a.zip"), (uuid.uuid4(), "project-exports/b.zip")]
    session = _Session(jobs)
    removed = asyncio.run(svc.remove_project_exports_listing(session, store, user_id=member))
    assert removed == 1 and store.deleted == ["project-exports/a.zip"]
    # the e-mail and the member line are gone from the store with the archive; the other export is untouched
    remaining = b"".join(store.objects.values())
    assert b"erin@people.example.test" not in remaining and str(member).encode() not in remaining
    assert list(store.objects) == ["project-exports/b.zip"]
    (params,) = session.updates
    assert params["status"] == "expired" and params["error_code"] == "subject_erased" and params["storage_key"] is None


def test_the_inventory_says_an_export_or_upload_may_carry_their_email() -> None:
    by_key = {location.key: location for location in INVENTORY}
    for key in ("objects:project-exports/", "objects:project-imports/"):
        assert "e-mail address" in by_key[key].reason, key
    assert by_key["objects:project-exports/"].erase == "delete"
    assert by_key["objects:project-imports/"].erase == "keep_record"
