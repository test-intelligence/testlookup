"""GAP-B10: the Content-Security-Policy and Referrer-Policy VALUES stay strict.

``regression/test_frontend_security_headers.py`` checks directive NAMES and
``regression/test_nginx_configs.py`` checks that the headers exist on every
location (and HSTS's max-age). Nothing read the values that make the CSP
worth having: ``'unsafe-inline'`` or a remote origin in ``script-src``, a
widened ``object-src`` / ``base-uri`` / ``form-action``, ``'unsafe-eval'``
anywhere (the only checks were two ``not.toContain('unsafe-eval')`` in
Playwright specs), or a looser Referrer-Policy all passed. Stored XSS from
test output (error text, names) is what this policy contains.

Reads ``frontend/index.html`` (the meta CSP the SPA ships) and the three
nginx configs the image, the K3s overlay and the OpenShift overlay serve.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "frontend" / "index.html"
REFERRER = "strict-origin-when-cross-origin"

#: Directive -> the only source list it may have.
PINNED = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
}
#: Sources a directive not pinned above may use: no remote origin, no wildcard.
ALLOWED_SOURCES = {
    "'self'",
    "'none'",
    "data:",
    "blob:",
    "ws:",
    "wss:",
    "'unsafe-inline'",
}


def _read(path: Path) -> str:
    # A missing file fails (never skips): every CI checkout has the whole repository.
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.http_equiv: dict[str, str] = {}
        self.named: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if "http-equiv" in attributes:
            self.http_equiv[attributes["http-equiv"].lower()] = attributes.get(
                "content", ""
            )
        if "name" in attributes:
            self.named[attributes["name"].lower()] = attributes.get("content", "")


@pytest.fixture(scope="module")
def meta() -> _Meta:
    parser = _Meta()
    parser.feed(_read(INDEX))
    return parser


def _directives(policy: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            name = tokens[0].lower()
            assert name not in out, f"directive {name} appears twice in {policy!r}"
            out[name] = tokens[1:]
    return out


@pytest.fixture(scope="module")
def csp(meta) -> dict[str, list[str]]:
    policy = meta.http_equiv.get("content-security-policy")
    assert policy, "frontend/index.html ships no Content-Security-Policy meta tag"
    return _directives(policy)


@pytest.mark.parametrize("directive", sorted(PINNED))
def test_the_pinned_directives_have_exactly_their_sources(csp, directive):
    assert csp.get(directive) == PINNED[directive], (
        f"{directive} is {csp.get(directive)!r}; it must stay {PINNED[directive]!r}"
    )


def test_no_directive_allows_eval_or_a_remote_origin(csp):
    loose = {
        name: [s for s in sources if s not in ALLOWED_SOURCES]
        for name, sources in csp.items()
        if any(s not in ALLOWED_SOURCES for s in sources)
    }
    assert not loose, f"sources outside {sorted(ALLOWED_SOURCES)}: {loose}"


def test_only_style_src_may_inline(csp):
    inlining = sorted(n for n, sources in csp.items() if "'unsafe-inline'" in sources)
    assert inlining in ([], ["style-src"]), inlining


def test_the_meta_referrer_is_strict(meta):
    assert meta.named.get("referrer") == REFERRER


def _nginx_configs() -> dict[str, str]:
    def configmap(overlay: str) -> str:
        path = REPO / "k8s" / "overlays" / overlay / "frontend-nginx-configmap.yaml"
        return yaml.safe_load(_read(path))["data"]["default.conf"]

    return {
        "image template": _read(REPO / "frontend" / "nginx.conf.template"),
        "k3s overlay": configmap("k3s"),
        "openshift overlay": configmap("openshift-artifactory"),
    }


def _header_values(conf: str, header: str) -> list[str]:
    return re.findall(
        rf'^\s*add_header\s+{re.escape(header)}\s+"([^"]*)"', conf, re.MULTILINE
    )


@pytest.mark.parametrize("name", ["image template", "k3s overlay", "openshift overlay"])
def test_every_nginx_referrer_policy_is_strict(name):
    values = _header_values(_nginx_configs()[name], "Referrer-Policy")

    assert values, f"{name} sends no Referrer-Policy"
    assert set(values) == {REFERRER}, values


@pytest.mark.parametrize("name", ["image template", "k3s overlay", "openshift overlay"])
def test_every_nginx_csp_header_forbids_framing_and_nothing_looser(name):
    values = _header_values(_nginx_configs()[name], "Content-Security-Policy")

    assert values, f"{name} sends no Content-Security-Policy header"
    assert set(values) == {"frame-ancestors 'none'"}, values
