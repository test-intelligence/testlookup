"""One-time link tokens shared by invitations (ADM-01) and password resets (ADM-02).

Two rules, held here so the two flows cannot drift apart:

* **Hash at rest.** Only ``sha256(token)`` is stored. A database dump, a backup
  or a log line holding the hash cannot be turned back into a working link.
  The lookup is by hash equality on a unique index: the attacker controls the
  input, not the stored value, so a timing difference reveals nothing usable.
* **Fragment links.** The raw token rides after ``#`` in the link
  (``/accept-invite#token=…``). Browsers never send the fragment to the server,
  so it does not reach access logs, proxies or a ``Referer`` header; the page
  reads it and posts it in a JSON body.
"""
from __future__ import annotations

import hashlib
import secrets

from app.core.config import settings

#: The same generic answer for every reason a link cannot be redeemed (unknown,
#: tampered, expired, used, revoked) — a different message per reason would be
#: an oracle for which tokens once existed.
INVALID_LINK_DETAIL = "This link is invalid, expired or already used."


def hash_token(raw: str) -> str:
    """sha256 hex of a raw token (64 chars, fits ``VARCHAR(64)``)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    """A fresh ``(raw, sha256 hex)`` pair. 32 bytes of entropy."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def fragment_link(path: str, raw: str) -> str:
    """``{public_base_url}{path}#token={raw}`` — the token never in a query string."""
    return f"{settings.public_base_url}{path}#token={raw}"


def hash_ip(address: str | None) -> str | None:
    """sha256 of a client address, so a request can be correlated without storing it."""
    if not address:
        return None
    return hashlib.sha256(address.encode("utf-8")).hexdigest()
