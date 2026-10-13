"""The browser (HTML / clone) root for a GitHub API base URL.

One rule for every place that turns the configured API base into a link or a
clone host: github.com when the API base IS ``api.github.com`` -- decided by
the URL's hostname -- and GitHub Enterprise's ``https://host/api/v3`` becomes
``https://host``.

The check used to be a substring (``"api.github.com" in base``), so a base such
as ``https://evil.example/api.github.com`` or ``https://api.github.com.evil.example``
was taken for public GitHub (CodeQL: incomplete URL substring sanitization).

The settings field is free text, so a base typed without a scheme
(``api.github.com``) is still public GitHub: its hostname is read as if the
scheme were there. Any other scheme-less base is left as typed -- the Fixer
puts its token only into an ``https://`` clone URL, and inventing one would
send it to a host the SSRF guard refuses for the API calls.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

PUBLIC_GITHUB = "https://github.com"
_PUBLIC_API_HOST = "api.github.com"


def github_html_root(api_base_url: Optional[str]) -> str:
    """``https://github.com`` for the public API (or none configured); otherwise
    the Enterprise host the API base names, without its ``/api/v3`` or ``/api``."""
    base = (api_base_url or "").strip().rstrip("/")
    if not base:
        return PUBLIC_GITHUB
    try:
        hostname = urlsplit(base).hostname
        if hostname is None and "://" not in base:
            # "api.github.com" parses as a bare path; "//" makes it the netloc.
            hostname = urlsplit("//" + base).hostname
    except ValueError:
        hostname = None
    if hostname == _PUBLIC_API_HOST:
        return PUBLIC_GITHUB
    root = base
    for suffix in ("/api/v3", "/api"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return root.rstrip("/")
