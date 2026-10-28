"""``@username`` mentions on test-case comments (W21d ACT-02).

A comment's ``@name`` resolves ONLY to an active, non-synthetic, non-service
member of the comment's CASE's project; an unknown or non-member name stays
plain text (no row, no notification, no error). The author is never their own
mention, and a comment names at most ``MAX_MENTIONS_PER_COMMENT`` people.

Mentions are stored as user IDS (the ``test_case.commented`` ledger row's
``context.mentions``), never usernames or e-mails.

Each mentioned member gets ONE in-app ``NotificationLog`` row (the bell):
``channel = "in_app"``, ``status = "sent"``, ``sent_at = now``. The delivery
relay claims only ``status = 'pending'`` rows (or a lease-expired ``sending``,
``manager.claim_pending_notification_deliveries``), so a mention row can never
be mailed, posted to Slack or to Teams -- the guarantee is structural, and
``tests/integration/test_test_case_mentions_postgres.py`` proves it.

Stage-only: nothing here commits, rolls back or opens a session. The comment
route (``routers/test_management_cases.add_comment``) owns the one commit, so
the ledger row and the notifications commit with the comment or not at all.

Logs carry ids and counts only: never the comment's text, a username or an
e-mail.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import structlog
from sqlalchemy import select

from app.models.postgres import NotificationLog, ProjectMember, User
from app.services.activity.live_vocab import (
    IN_APP_CHANNEL,
    MAX_MENTIONS_PER_COMMENT,
    MENTION_BODY_CHARS,
    MENTION_NOTIFICATION_EVENT,
    MENTION_TITLE,
)
from app.services.notification.gate_notifications import TITLE_MAX_CHARS, clip

logger = structlog.get_logger("services.activity.mentions")

#: A character that may sit right before an ``@`` without making it part of a
#: word (``email@alice`` is not a mention): anything outside this class.
_WORD_BEFORE = "A-Za-z0-9_.@-"
#: A character that may NOT follow a username (``@alice2`` is not ``alice``);
#: ``.`` may (``thanks @alice.``), and so may ``@``.
_WORD_AFTER = "A-Za-z0-9_-"


@dataclass(frozen=True, slots=True)
class MentionedMember:
    user_id: uuid.UUID
    username: str
    display_name: str


def find_mentions(text: str, usernames: Iterable[str]) -> list[str]:
    """The usernames ``text`` mentions, canonical case, first-seen order, de-duplicated, at most 10.

    Pure. At each ``@`` that starts the text or follows a character outside
    ``[A-Za-z0-9_.@-]``, the LONGEST username (case-insensitive) matching
    there and not followed by ``[A-Za-z0-9_-]`` is the mention. Usernames may
    hold ``.``, ``-`` and ``@`` (SSO / SCIM names are e-mails), so
    ``@bob@corp.com`` names ``bob@corp.com`` when that is a member.
    """
    if not text or "@" not in text:
        return []
    by_lower: dict[str, list[str]] = {}
    for name in usernames:
        if isinstance(name, str) and name:
            by_lower.setdefault(name.lower(), []).append(name)
    if not by_lower:
        return []
    # Longest first: an alternation tries its branches in order, so the first
    # branch that matches AND passes the look-ahead is the longest such name.
    alternatives = sorted(by_lower, key=lambda n: (-len(n), n))
    pattern = re.compile(
        rf"(?<![{_WORD_BEFORE}])@({'|'.join(re.escape(n) for n in alternatives)})(?![{_WORD_AFTER}])",
        re.IGNORECASE,
    )
    found: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        typed = match.group(1)
        candidates = by_lower.get(typed.lower())
        if not candidates:
            continue
        # Two stored names that differ only in case: the one typed exactly wins.
        canonical = typed if typed in candidates else sorted(candidates)[0]
        if canonical in seen:
            continue
        seen.add(canonical)
        found.append(canonical)
        if len(found) >= MAX_MENTIONS_PER_COMMENT:
            break
    return found


def _display_name(user: Any) -> str:
    """A person's name for the bell's title: full name, else username -- never an e-mail."""
    return str(getattr(user, "full_name", None) or getattr(user, "username", None) or "Someone")


async def resolve_mentions(
    db: Any,
    *,
    project_id: uuid.UUID,
    text: str,
    author_id: uuid.UUID | None,
) -> list[MentionedMember]:
    """The members of ``project_id`` that ``text`` mentions, in the order they appear.

    No ``@`` in ``text`` -> ``[]`` with NO query. Otherwise ONE select of the
    project's active, non-synthetic, non-service members (the rule the
    composer's suggestions apply too). The author takes part in the parse, so
    ``@ana.b`` written by ``ana.b`` never falls back to a member ``ana``, and
    is then dropped: nobody is their own mention.
    """
    if not isinstance(text, str) or "@" not in text:
        return []
    result = await db.execute(
        select(User.id, User.username, User.full_name)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(
            ProjectMember.project_id == project_id,
            User.is_active.is_(True),
            User.is_synthetic.is_(False),
            User.is_service_account.is_(False),
        )
    )
    members: dict[str, tuple[uuid.UUID, str | None]] = {}
    for user_id, username, full_name in result.all():
        if isinstance(username, str) and username:
            members[username] = (user_id, full_name)
    resolved: list[MentionedMember] = []
    for username in find_mentions(text, members):
        user_id, full_name = members[username]
        if author_id is not None and user_id == author_id:
            continue
        resolved.append(MentionedMember(user_id=user_id, username=username, display_name=full_name or username))
    return resolved


def mention_delivery_key(comment_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """One in-app row per mentioned member per comment (``uq_notification_log_delivery_key``)."""
    return hashlib.sha256(f"mention:{comment_id}:{user_id}".encode()).hexdigest()


async def stage_mention_notifications(
    db: Any,
    *,
    members: Sequence[MentionedMember],
    case: Any,
    comment: Any,
    author: Any,
) -> int:
    """``db.add`` one in-app ``NotificationLog`` per member; returns how many. No members -> no session call.

    ``status = "sent"`` and ``sent_at = now``: the row IS the bell entry, and
    the delivery relay (which claims only ``pending`` rows) can never pick it
    up, so nothing is ever e-mailed. ``delivery_metadata.app_path`` is the one
    link the bell may open (``gate_notifications.APP_PATH_RE``).
    """
    if not members:
        return 0
    now = datetime.now(timezone.utc)
    title = clip(MENTION_TITLE.format(author=_display_name(author), case=case.title), TITLE_MAX_CHARS)
    body = clip(comment.content, MENTION_BODY_CHARS)
    app_path = f"/test-management?case={case.id}"
    for member in members:
        db.add(
            NotificationLog(
                user_id=member.user_id,
                project_id=case.project_id,
                channel=IN_APP_CHANNEL,
                event_type=MENTION_NOTIFICATION_EVENT,
                title=title,
                body=body,
                status="sent",
                sent_at=now,
                is_read=False,
                delivery_key=mention_delivery_key(comment.id, member.user_id),
                delivery_metadata={"app_path": app_path, "comment_id": str(comment.id)},
            )
        )
    logger.info(
        "test_case_mentions_staged",
        case_id=str(case.id),
        comment_id=str(comment.id),
        count=len(members),
    )
    return len(members)
