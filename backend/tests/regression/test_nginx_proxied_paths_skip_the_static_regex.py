"""A proxied path is never answered by the static-file location.

The rule: every request under ``/api/``, ``/ws/`` and ``/webhooks/`` reaches
the backend, whatever its last characters are. nginx checks regex locations
after it has found the longest matching prefix location, and a regex match
wins, unless that prefix location is marked ``^~``. The static-asset location
is a regex on the file extension (``\\.(js|css|png|...)$``), so an API path
that ends in ``.png`` or ``.js`` (an artefact download, a file name in a
path) was answered from disk: a 404 with a one-year cache header.

All three copies of the server config are checked (the image's template and
the two ConfigMap copies), with nginx's own selection order applied to sample
request paths.
"""
from __future__ import annotations

import re

import pytest
from tests.regression.test_nginx_configs import CONFIGS, _locations, _servers, parse

pytestmark = pytest.mark.regression

SERVER_CONFIGS = (
    "frontend/nginx.conf.template",
    "k8s/overlays/k3s/frontend-nginx-configmap.yaml:default.conf",
    "k8s/overlays/openshift-artifactory/frontend-nginx-configmap.yaml:default.conf",
)
PROXIED_PREFIXES = ("/api/", "/api/v1/project-data-downloads/", "/ws/", "/webhooks/")


def select(locations: dict[str, list], path: str) -> str:
    """The location nginx serves ``path`` from (ngx_http_core_find_location):
    an exact match; else the longest prefix, final if it is ``^~``; else the
    first matching regex in file order; else that longest prefix."""
    if f"= {path}" in locations:
        return f"= {path}"
    best = ""
    for key in locations:
        modifier, _, prefix = key.rpartition(" ")
        if modifier in ("", "^~") and path.startswith(prefix) and len(prefix) > len(best.rpartition(" ")[2]):
            best = key
    if best.startswith("^~ "):
        return best
    for key in locations:
        modifier, _, pattern = key.partition(" ")
        if modifier in ("~", "~*") and re.search(pattern, path, re.I if modifier == "~*" else 0):
            return key
    return best


def _proxies(location: list) -> bool:
    return any(name == "proxy_pass" for name, _, _ in location)


@pytest.mark.parametrize("name", SERVER_CONFIGS)
def test_every_proxied_prefix_is_marked_final(name: str) -> None:
    for server in _servers(parse(CONFIGS[name])):
        locations = _locations(server)
        proxied = {key for key, children in locations.items() if _proxies(children)}
        assert proxied == {f"^~ {prefix}" for prefix in PROXIED_PREFIXES}, (name, sorted(proxied))


@pytest.mark.parametrize("name", SERVER_CONFIGS)
@pytest.mark.parametrize(
    "path, expected",
    [
        ("/api/v1/artifacts/7/screenshot.png", "^~ /api/"),
        ("/api/v1/reports/9/bundle.js", "^~ /api/"),
        ("/api/v1/projects/3/logo.SVG", "^~ /api/"),
        ("/api/v1/project-data-downloads/0b9f3c1e-6a5d-4f7e-9a51-2f3c4d5e6f70.css",
         "^~ /api/v1/project-data-downloads/"),
        ("/ws/live/run-1.js", "^~ /ws/"),
        ("/webhooks/minio.ico", "^~ /webhooks/"),
        ("/webhooks/ci/build.woff2", "^~ /webhooks/"),
    ],
)
def test_a_proxied_path_that_ends_like_a_static_file_still_reaches_the_backend(
    name: str, path: str, expected: str
) -> None:
    for server in _servers(parse(CONFIGS[name])):
        locations = _locations(server)
        chosen = select(locations, path)
        assert chosen == expected, (name, path, chosen)
        assert _proxies(locations[chosen])


@pytest.mark.parametrize("name", SERVER_CONFIGS)
@pytest.mark.parametrize(
    "path, expected_prefix",
    [
        ("/assets/index-4f2a.js", "~*"),
        ("/assets/index-4f2a.css", "~*"),
        ("/favicon.svg", "~*"),
        ("/og-card-2.png", "~*"),
        ("/index.html", "= /index.html"),
        ("/projects/3/runs", "/"),
        # Not under a proxied prefix: "/apiary.js" and "/api" itself are the SPA's.
        ("/apiary.js", "~*"),
        ("/api", "/"),
    ],
)
def test_static_files_and_client_routes_are_served_as_before(
    name: str, path: str, expected_prefix: str
) -> None:
    """Positive case: the static location still gets every real static file,
    index.html keeps its exact match, and client routes fall back to ``/``."""
    for server in _servers(parse(CONFIGS[name])):
        locations = _locations(server)
        chosen = select(locations, path)
        assert chosen == expected_prefix or chosen.startswith(expected_prefix + " "), (name, path, chosen)
        assert not _proxies(locations[chosen])


def test_the_selection_model_follows_nginx() -> None:
    """Self-test: a regex beats a plain prefix and loses to a ``^~`` prefix;
    the longest prefix decides, so a plain longer prefix re-opens the regex."""
    plain = _locations(_servers(parse(
        "server { location /api/ { } location ~* \\.(js)$ { } location / { } }"
    ))[0])
    assert select(plain, "/api/x.js") == "~* \\.(js)$"
    assert select(plain, "/api/x") == "/api/"
    final = _locations(_servers(parse(
        "server { location ^~ /api/ { } location /api/v1/files/ { } location ~* \\.(js)$ { } location / { } }"
    ))[0])
    assert select(final, "/api/x.js") == "^~ /api/"
    assert select(final, "/api/v1/files/x.js") == "~* \\.(js)$"
    assert select(final, "/x.js") == "~* \\.(js)$"
    assert select(final, "/x") == "/"
