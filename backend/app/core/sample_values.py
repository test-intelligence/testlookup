"""Shipped sample values are not configuration (D-76, D-33).

``.env.example`` used to ship live-looking assignments for every optional
integration (``JIRA_DOMAIN=yourcompany.atlassian.net``,
``SMTP_PASSWORD=your-app-password``, ...). The installer copies that file to
``.env``, so on an install that predates the commented-out template the
backend received every placeholder as if an operator had typed it, and the
settings pages said "Password saved." and "A webhook is set" about settings
nobody made.

``is_sample_value`` recognises those shipped placeholders; ``configured_value``
turns one into ``None`` so every place that decides "is this configured?"
reads it as not configured. Real values pass through unchanged.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: Substrings only a shipped placeholder carries.
_SAMPLE_MARKERS = (
    "yourcompany",
    "yourorg",
    "replace-with",
    "change-me",
)

#: ``your-`` only where the templates put it: at the start of the value or of
#: a host label (``your-app-password``, ``https://your-splunk-instance``,
#: ``api.your-cluster.com``, ``xoxb-your-slack-bot-token``). Inside a word it
#: is a real value someone chose (``#ask-your-team``).
_YOUR_PREFIX = re.compile(r"(?:^|://|[.@]|xoxb-)your-")

#: The reserved example domains (RFC 2606) as a host or an e-mail domain:
#: ``smtp.example.com``, ``you@example.com``. ``.example.test`` and other
#: names stay real (tests and private installs use them).
_EXAMPLE_DOMAIN = re.compile(r"(?:^|[.@/])example\.(?:com|org|net)(?:$|[:/])")

#: A whole value in angle brackets: ``<set-a-strong-password>``.
_ANGLE_PLACEHOLDER = re.compile(r"<[a-z0-9][a-z0-9 ._-]*>")


def is_sample_value(value: Any) -> bool:
    """True when ``value`` is one of the placeholders the install templates ship."""
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    if not text:
        return False
    if any(marker in text for marker in _SAMPLE_MARKERS) or _YOUR_PREFIX.search(text):
        return True
    return bool(_EXAMPLE_DOMAIN.search(text) or _ANGLE_PLACEHOLDER.fullmatch(text))


def configured_value(value: Any) -> Optional[Any]:
    """``value``, or ``None`` when it is a shipped placeholder."""
    return None if is_sample_value(value) else value
