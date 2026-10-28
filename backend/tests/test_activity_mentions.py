"""W21d ACT-02: ``@username`` mentions on test-case comments (``services/activity/mentions.py``).

``find_mentions`` is pure and checked on real-shaped samples (the frontend's
``mentionModel.test.ts`` mirrors the set). ``resolve_mentions`` issues NO query
without an ``@`` and ONE select of the case project's active, non-synthetic,
non-service members otherwise; the author is never their own mention.
``stage_mention_notifications`` stages one in-app, already-``sent`` bell row
per member, which the delivery relay can never claim.

Test usernames start with a letter and never look like an e-mail or a phone
number by chance (the SSO samples are e-mail SHAPED on purpose).
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.activity import mentions as M
from app.services.activity.live_vocab import (
    IN_APP_CHANNEL,
    MAX_MENTIONS_PER_COMMENT,
    MENTION_NOTIFICATION_EVENT,
)

MEMBERS = ["alice", "alice.smith", "bob", "bob@corp.com", "carol-ops", "dan_qa", "Erin"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("@alice please re-check step 3", ["alice"]),
        ("thanks @alice.", ["alice"]),
        ("(@alice)", ["alice"]),
        ("@Alice", ["alice"]),
        ("@ALICE and @alice", ["alice"]),
        ("@alice.smith can you look", ["alice.smith"]),
        ("@alice.smith, then @alice", ["alice.smith", "alice"]),
        ("email@alice is not a mention", []),
        ("a.b@alice and x_y@bob", []),
        ("first.@alice and first-@bob", []),
        ("@alice2 is someone else", []),
        ("@alice_b is someone else", []),
        ("@alice-ops is someone else", []),
        ("@bob@corp.com owns this", ["bob@corp.com"]),
        ("@bob, not @bob@corp.com", ["bob", "bob@corp.com"]),
        ("@carol-ops and @dan_qa", ["carol-ops", "dan_qa"]),
        ("@erin", ["Erin"]),
        ("@ alice has a space", []),
        ("no mention at all", []),
        ("@@alice", []),
        ("@alice\n@bob", ["alice", "bob"]),
        ("@zed is not a member", []),
        ("", []),
    ],
)
def test_find_mentions_on_real_shaped_samples(text, expected):
    assert M.find_mentions(text, MEMBERS) == expected


def test_eleven_distinct_mentions_keep_the_first_ten():
    names = [f"user{chr(ord('a') + i)}x" for i in range(11)]
    text = " ".join(f"@{n}" for n in names)
    found = M.find_mentions(text, names)
    assert MAX_MENTIONS_PER_COMMENT == 10
    assert found == names[:10]


def test_a_name_mentioned_twice_is_listed_once_in_first_seen_order():
    assert M.find_mentions("@bob @alice @bob @alice", MEMBERS) == ["bob", "alice"]


def test_no_member_names_finds_nothing_and_regex_characters_are_literal():
    assert M.find_mentions("@alice", []) == []
    assert M.find_mentions("@a.c", ["abc"]) == []          # "." in a name is not a wildcard
    assert M.find_mentions("@a+b", ["a+b"]) == ["a+b"]


def test_two_stored_names_differing_only_in_case_the_typed_one_wins():
    assert M.find_mentions("@Sam and @sam", ["sam", "Sam"]) == ["Sam", "sam"]


# ── resolve_mentions ─────────────────────────────────────────────────────────

PROJECT = uuid.UUID("5a5a5a5a-1111-4111-8111-111111111111")


def _member_rows(*rows):
    result = MagicMock()
    result.all.return_value = list(rows)
    return result


@pytest.mark.asyncio
async def test_no_at_sign_means_no_query():
    db = SimpleNamespace(execute=AsyncMock())
    assert await M.resolve_mentions(db, project_id=PROJECT, text="Looks right", author_id=uuid.uuid4()) == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_select_of_the_projects_eligible_members():
    alice, bob = uuid.uuid4(), uuid.uuid4()
    db = SimpleNamespace(execute=AsyncMock(return_value=_member_rows(
        (alice, "alice", "Alice Ng"), (bob, "bob", None),
    )))
    found = await M.resolve_mentions(db, project_id=PROJECT, text="@bob and @alice and @zed", author_id=None)
    assert found == [
        M.MentionedMember(user_id=bob, username="bob", display_name="bob"),
        M.MentionedMember(user_id=alice, username="alice", display_name="Alice Ng"),
    ]
    db.execute.assert_awaited_once()
    from sqlalchemy.dialects import postgresql

    sql = str(db.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    sql = re.sub(r"\s+", " ", sql)
    assert "JOIN project_members ON project_members.user_id = users.id" in sql
    assert "project_members.project_id = %(project_id_1)s" in sql
    assert "users.is_active IS true" in sql
    assert "users.is_synthetic IS false" in sql
    assert "users.is_service_account IS false" in sql
    assert "email" not in sql


@pytest.mark.asyncio
async def test_the_author_is_never_their_own_mention_and_still_shadows_a_shorter_name():
    author, ana = uuid.uuid4(), uuid.uuid4()
    db = SimpleNamespace(execute=AsyncMock(return_value=_member_rows(
        (author, "ana.b", "Ana B"), (ana, "ana", "Ana"),
    )))
    # "@ana.b" written by ana.b is the author (dropped), never a fallback to "ana".
    assert await M.resolve_mentions(db, project_id=PROJECT, text="cc @ana.b", author_id=author) == []
    db.execute.return_value = _member_rows((author, "ana.b", "Ana B"), (ana, "ana", "Ana"))
    found = await M.resolve_mentions(db, project_id=PROJECT, text="@ana.b @ana", author_id=author)
    assert [m.user_id for m in found] == [ana]


# ── stage_mention_notifications ──────────────────────────────────────────────


def _case():
    return SimpleNamespace(id=uuid.UUID("c0ffee00-2222-4222-8222-222222222222"), project_id=PROJECT, title="Login works")


def _comment(content="@alice please re-check step 3"):
    return SimpleNamespace(id=uuid.UUID("bead0000-3333-4333-8333-333333333333"), content=content)


@pytest.mark.asyncio
async def test_no_members_touch_no_session_attribute():
    db = SimpleNamespace()   # any attribute access would raise
    assert await M.stage_mention_notifications(
        db, members=[], case=_case(), comment=_comment(), author=SimpleNamespace(full_name="Bob", username="bob"),
    ) == 0


@pytest.mark.asyncio
async def test_one_in_app_sent_row_per_member_that_no_relay_can_claim():
    added = []
    db = SimpleNamespace(add=added.append)
    alice, carol = uuid.uuid4(), uuid.uuid4()
    members = [
        M.MentionedMember(user_id=alice, username="alice", display_name="Alice Ng"),
        M.MentionedMember(user_id=carol, username="carol-ops", display_name="Carol"),
    ]
    case, comment = _case(), _comment("x" * 300)
    n = await M.stage_mention_notifications(
        db, members=members, case=case, comment=comment, author=SimpleNamespace(full_name="Ana", username="ana"),
    )
    assert n == 2 and len(added) == 2
    for row, member in zip(added, members):
        assert row.user_id == member.user_id
        assert row.project_id == PROJECT
        assert row.channel == IN_APP_CHANNEL == "in_app"
        assert row.event_type == MENTION_NOTIFICATION_EVENT == "test_case.mentioned"
        assert row.title == "Ana mentioned you on Login works"
        assert row.body == "x" * 279 + "…"
        assert row.status == "sent" and row.sent_at is not None and row.is_read is False
        assert re.fullmatch(r"[0-9a-f]{64}", row.delivery_key)
        assert row.delivery_key == M.mention_delivery_key(comment.id, member.user_id)
        assert row.delivery_metadata == {
            "app_path": f"/test-management?case={case.id}",
            "comment_id": str(comment.id),
        }
        assert row.next_delivery_at is None and row.delivery_token is None
    assert added[0].delivery_key != added[1].delivery_key


@pytest.mark.asyncio
async def test_the_title_names_the_author_never_an_email_and_is_clipped():
    added = []
    member = M.MentionedMember(user_id=uuid.uuid4(), username="alice", display_name="Alice")
    case = SimpleNamespace(id=uuid.uuid4(), project_id=PROJECT, title="T" * 400)
    await M.stage_mention_notifications(
        SimpleNamespace(add=added.append), members=[member], case=case, comment=_comment(),
        author=SimpleNamespace(full_name=None, username="bob", email="bob@corp.example"),
    )
    assert added[0].title.startswith("bob mentioned you on TTT")
    assert len(added[0].title) == 200 and added[0].title.endswith("…")
    assert "corp.example" not in added[0].title


def test_mentions_module_is_stage_only():
    import inspect

    source = inspect.getsource(M)
    assert ".commit(" not in source and ".rollback(" not in source
    assert "AsyncSessionLocal" not in source
