"""CLI output must not crash on a console that cannot encode its glyphs.

``testlookup auth login`` exited **1 with a traceback** on a Windows cp1252
console — *after* the login had already succeeded and the profile was saved::

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u2713'
    in position 0: character maps to <undefined>

So the credentials were fine and the command reported failure anyway. A CI
wrapper reads that exit code, not the profile on disk.

Measured per helper with ``PYTHONIOENCODING=cp1252``:

===================  =======  ========  ======
helper               glyph    stream    result
===================  =======  ========  ======
``print_success``    ``✓``    stdout    **exit 1, UnicodeEncodeError**
``print_error``      ``✗``    stderr    exit 0
``print_warning``    ``⚠``    stderr    exit 0
===================  =======  ========  ======

Rich already degrades its *own* rendering — a full ``projects list`` table
renders fine under cp1252, because Rich substitutes ASCII box-drawing when the
encoding cannot carry the Unicode characters. What it does not do is rescue
literal glyphs handed to it inside markup. Those are just text.

The fix picks the glyph against the destination stream's real encoding, so a
UTF-8 terminal still gets ``✓`` and a legacy console gets ``[OK]``. It is
deliberately not a global ``sys.stdout.reconfigure`` — mutating the process's
streams from a library import is a much larger blast radius than choosing a
character, and would change byte-for-byte output for every consumer.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from testlookup_cli import output


def _run(snippet: str, encoding: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": encoding},
    )


@pytest.mark.parametrize("fn", ["print_success", "print_error", "print_warning"])
def test_helpers_survive_a_legacy_console(fn: str):
    """The measured crash, plus its siblings so the fix is not partial."""
    proc = _run(f"from testlookup_cli import output; output.{fn}('hello')", "cp1252")
    err = proc.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in err, (
        f"output.{fn} crashed on a cp1252 console: {err[-300:]}"
    )
    assert proc.returncode == 0, (
        f"output.{fn} exited {proc.returncode} on a cp1252 console — a CI "
        f"wrapper reads that as failure even when the command succeeded"
    )


@pytest.mark.parametrize("fn", ["print_success", "print_error", "print_warning"])
def test_helpers_still_work_on_utf8(fn: str):
    """The fallback must not degrade a capable terminal."""
    proc = _run(f"from testlookup_cli import output; output.{fn}('hello')", "utf-8")
    assert proc.returncode == 0
    assert b"hello" in proc.stdout + proc.stderr


class TestGlyphSelection:
    def test_utf8_keeps_the_unicode_glyph(self):
        class _S:
            encoding = "utf-8"

        assert output._glyph("✓", "[OK]", _S()) == "✓"

    def test_cp1252_falls_back_to_ascii(self):
        class _S:
            encoding = "cp1252"

        assert output._glyph("✓", "[OK]", _S()) == "[OK]"

    def test_a_stream_with_no_encoding_is_treated_as_capable(self):
        """StringIO and friends have no .encoding; assume UTF-8 rather than
        degrading output for every test harness that captures stdout."""

        class _S:
            pass

        assert output._glyph("✓", "[OK]", _S()) == "✓"

    def test_an_unknown_encoding_does_not_raise(self):
        """A bogus PYTHONIOENCODING must not turn a message into a crash."""

        class _S:
            encoding = "not-a-real-codec"

        assert output._glyph("✓", "[OK]", _S()) == "[OK]"
