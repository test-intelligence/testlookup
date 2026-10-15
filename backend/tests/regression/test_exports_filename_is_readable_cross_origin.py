"""EXEC-06: a cross-origin SPA can read an export's filename.

The CSV/JSON exports name their file in ``Content-Disposition`` and the UI saves the download under that name
(``filenameFromDisposition`` in the execution-metrics and value-metrics services). A header the CORS policy does
not expose does not exist to the browser's script, so a deployment serving the SPA from another origin saved every
export under the client's fallback name.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.regression


def test_cors_exposes_content_disposition():
    from starlette.middleware.cors import CORSMiddleware

    from app.main import app

    exposed: set = set()
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            exposed = set(middleware.kwargs.get("expose_headers", ()))
    assert "Content-Disposition" in exposed, exposed
