"""Clickjacking protection must be a RESPONSE HEADER, not a <meta> tag.

Found by exploratory testing against the live homelab (2026-08-07). Chromium
logged on every page load:

    The Content Security Policy directive 'frame-ancestors' is ignored when
    delivered via a <meta> element.

`frontend/index.html` sets CSP via `<meta http-equiv>` and its own comment states
the intent — "frame-ancestors 'none': prevent clickjacking (X-Frame-Options
equiv)" — but browsers ignore that directive in a meta tag, and the deployment
sent no CSP or X-Frame-Options header. The protection was inert.

The nginx trap this pins: `add_header` is inherited by a nested block ONLY when
that block declares no `add_header` of its own. A location that sets, say, a
Cache-Control header silently drops every inherited security header — including
`location = /index.html`, the one document that clickjacking actually targets.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]

REQUIRED = ("frame-ancestors", "X-Frame-Options", "X-Content-Type-Options")


def _template_conf() -> str:
    return (REPO / "frontend" / "nginx.conf.template").read_text(encoding="utf-8")


def _configmap_conf(overlay: str) -> str:
    path = REPO / "k8s" / "overlays" / overlay / "frontend-nginx-configmap.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["data"]["default.conf"]


ALL_CONFIGS = {
    "image template": _template_conf,
    "homelab overlay": lambda: _configmap_conf("homelab"),
    "openshift overlay": lambda: _configmap_conf("openshift-artifactory"),
}


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
@pytest.mark.parametrize("directive", REQUIRED)
def test_every_frontend_config_sends_the_header(name, directive):
    conf = ALL_CONFIGS[name]()
    assert directive in conf, (
        f"{name} does not send {directive!r} as a response header. A CSP <meta> "
        "tag cannot enforce frame-ancestors — the protection would be inert."
    )


@pytest.mark.parametrize("name", sorted(ALL_CONFIGS))
def test_locations_that_set_their_own_headers_repeat_the_security_headers(name):
    """The nginx inheritance trap, pinned.

    Walks each ``location`` block; any block that declares its own ``add_header``
    has opted out of inheritance and must therefore restate the security headers.
    """
    conf = ALL_CONFIGS[name]()

    # Split into location blocks by brace depth (configs are small and flat).
    blocks: list[tuple[str, str]] = []
    lines = conf.split("\n")
    i = 0
    while i < len(lines):
        m = re.match(r"\s*(location [^{]*)\{", lines[i])
        if not m:
            i += 1
            continue
        header, depth, body = m.group(1).strip(), 1, []
        i += 1
        while i < len(lines) and depth > 0:
            depth += lines[i].count("{") - lines[i].count("}")
            if depth > 0:
                body.append(lines[i])
            i += 1
        blocks.append((header, "\n".join(body)))

    assert blocks, f"parsed no location blocks out of {name} — parser is wrong"

    for header, body in blocks:
        if "add_header" not in body:
            continue  # inherits the server-level headers — fine
        for directive in REQUIRED:
            assert directive in body, (
                f"{name}: `{header}` declares its own add_header, so nginx gives it "
                f"NONE of the inherited security headers — but it does not restate "
                f"{directive!r}. Responses from this location would be unprotected."
            )


def _meta_csp_directives() -> str:
    """The CSP <meta> tag's *content* attribute only.

    Deliberately not a substring search over the whole file: index.html's
    comments discuss ``frame-ancestors`` at length (explaining why it is absent),
    and a naive ``"frame-ancestors" in index_html`` is satisfied by that prose.
    A test that a comment can fool is not a test.
    """
    index_html = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    m = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"',
        index_html,
    )
    assert m, "index.html no longer has a CSP meta tag — update this test"
    return m.group(1)


def test_meta_csp_does_not_declare_frame_ancestors():
    """Browsers IGNORE frame-ancestors in a meta tag and log an error for it on
    every page load. Keeping it there protects nothing and generates constant
    console noise that camouflages real CSP violations."""
    assert "frame-ancestors" not in _meta_csp_directives(), (
        "frame-ancestors is back in the meta CSP; it is inert there and logs a "
        "console error on every page load. The header in nginx is what enforces it."
    )


def test_the_header_still_carries_frame_ancestors():
    """Removing it from the meta tag is only safe because the header has it."""
    assert "frame-ancestors" in _template_conf(), (
        "the meta tag no longer declares frame-ancestors and neither does the "
        "served config — clickjacking protection would be gone entirely"
    )


def test_meta_csp_keeps_the_directives_that_do_work_there():
    """Removing one dead directive must not strip the effective ones."""
    directives = _meta_csp_directives()
    for kept in ("default-src", "script-src", "object-src", "base-uri", "form-action"):
        assert kept in directives, f"meta CSP lost {kept!r}"
