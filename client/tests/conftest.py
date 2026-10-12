"""Make the flat SDK modules (``testlookup_reporter``, ``ci_context``,
``commit_range``) importable when running ``pytest client/tests`` from the
repo root, and provide ``wire``: a fake TestLookup server (``sdk_wire.Wire``)
behind every httpx client the SDK builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

import testlookup_reporter as tr  # noqa: E402
from sdk_wire import LEAKY_ENV_NAMES, LEAKY_ENV_PREFIXES, Wire  # noqa: E402


@pytest.fixture
def wire(monkeypatch, tmp_path) -> Wire:
    """Route every SDK HTTP call to a fake server, in an empty directory with
    an empty home, so only the config a test writes is discovered."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for key in list(os.environ):
        if key.upper().startswith(LEAKY_ENV_PREFIXES) or key.upper() in LEAKY_ENV_NAMES:
            monkeypatch.delenv(key, raising=False)

    w = Wire()
    real_client = httpx.AsyncClient

    class RecordingClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            w.clients.append(dict(kwargs))
            kwargs["transport"] = httpx.MockTransport(w.handle)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingClient)

    def recording_commit_range(**kwargs: Any) -> None:
        w.commit_range_calls.append(kwargs)
        return None

    # Walking git is out of scope here (test_reporter_commit_range.py owns
    # it); record what each path hands the collector instead.
    monkeypatch.setattr(tr, "resolve_commit_range", recording_commit_range)
    return w
