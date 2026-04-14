"""Unit tests for app.services.sql_utils."""
from app.services.sql_utils import escape_like, like_contains


def test_escape_like_empty():
    assert escape_like("") == ""


def test_escape_like_plain_text_unchanged():
    assert escape_like("payment") == "payment"


def test_escape_like_escapes_underscore():
    # ``_`` is the LIKE single-char wildcard and must be escaped so a user
    # searching for ``test_case`` does not also match ``testXcase``.
    assert escape_like("test_case") == "test\\_case"


def test_escape_like_escapes_percent():
    # ``%`` is the LIKE any-chars wildcard; escaping prevents unbounded matches.
    assert escape_like("50%") == "50\\%"


def test_escape_like_escapes_backslash_first():
    # The escape character itself must be doubled, and must be doubled BEFORE
    # the % / _ escapes are introduced — otherwise the helper would turn a
    # freshly added ``\_`` into ``\\_``, which LIKE reads as "escaped
    # backslash followed by wildcard underscore".
    assert escape_like("a\\b") == "a\\\\b"
    assert escape_like("a\\_b") == "a\\\\\\_b"


def test_like_contains_wraps_with_percent():
    assert like_contains("foo") == "%foo%"


def test_like_contains_escapes_user_wildcards():
    assert like_contains("50%_off") == "%50\\%\\_off%"


def test_like_contains_empty_input():
    assert like_contains("") == "%%"
