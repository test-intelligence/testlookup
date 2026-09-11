"""Every nginx config in the repo, parsed (re-audit M26 + L1).

M26: no ``client_max_body_size`` (nginx's 1 MB default rejected report
uploads the backend accepts up to 50 MiB) and no ``index.html`` no-cache rule
in the image's own template. L1: no HSTS where TLS terminates.

nginx is not installed offline, so ``nginx -t`` cannot run here. The configs
are tokenized and checked for their EFFECTIVE values, including nginx's
``add_header`` inheritance rule: a location that declares any ``add_header``
inherits none from the server block.

The configs are enumerated from the tracked tree, not listed by hand: a new
nginx config (a file with a ``server {`` block, or a ConfigMap value holding
one) is checked the day it is added.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST = REPO_ROOT / "backend" / "app" / "routers" / "ingest.py"
HOMELAB_INGRESS = REPO_ROOT / "k8s" / "overlays" / "homelab" / "ingress-traefik.yaml"
ONE_YEAR = 31_536_000
SECURITY_HEADERS = {
    "Content-Security-Policy", "X-Frame-Options",
    "X-Content-Type-Options", "Referrer-Policy",
}

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / "k8s").exists(), reason="deployment configs not present"
)


# ── a small nginx parser ────────────────────────────────────────────────────

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[{};]|[^\s{};"\']+')


def _strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        in_quote = None
        for i, ch in enumerate(line):
            if ch in "\"'" and in_quote in (None, ch):
                in_quote = None if in_quote else ch
            elif ch == "#" and in_quote is None:
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def parse(text: str) -> list:
    """``[(name, [args], children-or-None)]``.

    ``${VAR}`` is replaced first, as the nginx image's envsubst does at
    container start (frontend/Dockerfile): its braces are not blocks.
    """
    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"envsubst-\1", text)
    tokens = _TOKEN.findall(_strip_comments(text))
    pos = 0

    def block() -> list:
        nonlocal pos
        items: list = []
        words: list[str] = []
        while pos < len(tokens):
            token = tokens[pos]
            pos += 1
            if token == ";":
                items.append((words[0], words[1:], None))
                words = []
            elif token == "{":
                items.append((words[0], words[1:], block()))
                words = []
            elif token == "}":
                assert not words, f"dangling tokens {words}"
                return items
            else:
                words.append(token.strip("\"'") if token[0] in "\"'" else token)
        assert not words, f"dangling tokens {words}"
        return items

    return block()


def _servers(tree: list) -> list[list]:
    servers = [children for name, _, children in tree if name == "server"]
    for name, _, children in tree:
        if name == "http" and children:
            servers += _servers(children)
    return servers


def _headers(directives: list) -> dict[str, str]:
    return {args[0]: args[1] for name, args, _ in directives if name == "add_header"}


def _effective_headers(server: list, location: list) -> dict[str, str]:
    own = _headers(location)
    return own if own else _headers(server)


def _locations(server: list) -> dict[str, list]:
    return {" ".join(args): children for name, args, children in server if name == "location"}


def _size_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+)([kKmMgG]?)", value)
    assert match, value
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2).lower()]
    return int(match.group(1)) * scale


# ── enumeration ─────────────────────────────────────────────────────────────

_SERVER_BLOCK = re.compile(r"^\s*server\s*\{", re.M)


def nginx_configs() -> dict[str, str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    )
    configs: dict[str, str] = {}
    for rel in proc.stdout.decode("utf-8").split("\0"):
        if not rel or "node_modules/" in rel:
            continue
        path = REPO_ROOT / rel
        if path.suffix in (".conf", ".template") or path.name.endswith(".conf.template"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if _SERVER_BLOCK.search(text):
                configs[rel] = text
        elif path.suffix in (".yaml", ".yml") and rel.startswith("k8s/"):
            text = path.read_text(encoding="utf-8")
            if not _SERVER_BLOCK.search(text):
                continue
            for doc in yaml.safe_load_all(text):
                if doc and doc.get("kind") == "ConfigMap":
                    for key, value in (doc.get("data") or {}).items():
                        if isinstance(value, str) and _SERVER_BLOCK.search(value):
                            configs[f"{rel}:{key}"] = value
    return configs


CONFIGS = nginx_configs()


def _is_homelab(name: str) -> bool:
    return name.startswith("k8s/overlays/homelab/")


def test_every_nginx_config_is_found() -> None:
    assert {
        "frontend/nginx.conf.template",
        "k8s/overlays/homelab/frontend-nginx-configmap.yaml:default.conf",
        "k8s/overlays/openshift-artifactory/frontend-nginx-configmap.yaml:default.conf",
    } <= set(CONFIGS)


def _backend_upload_cap() -> int:
    tree = ast.parse(INGEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") == "MAX_FILE_SIZE"):
            return int(eval(compile(ast.Expression(node.value), "cap", "eval")))  # noqa: S307
    raise AssertionError("MAX_FILE_SIZE not found in routers/ingest.py")


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_body_cap_is_aligned_with_the_backend(name: str) -> None:
    cap = _backend_upload_cap()
    assert cap == 50 * 1024 * 1024
    for server in _servers(parse(CONFIGS[name])):
        sizes = [args[0] for n, args, _ in server if n == "client_max_body_size"]
        assert len(sizes) == 1, f"{name}: server block needs one client_max_body_size"
        limit = _size_bytes(sizes[0])
        # Above the cap (so the backend's JSON 413 is what an oversize file
        # meets), but only by multipart headroom, not an open door.
        assert cap < limit <= cap + 8 * 1024 * 1024, (name, sizes[0])


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_index_html_is_never_cached_and_assets_are(name: str) -> None:
    for server in _servers(parse(CONFIGS[name])):
        locations = _locations(server)
        index = locations.get("= /index.html")
        assert index is not None, f"{name}: no `location = /index.html`"
        cache = _effective_headers(server, index).get("Cache-Control", "")
        assert "no-cache" in cache and "no-store" in cache, (name, cache)
        # Client routes reach index.html through the SPA fallback.
        try_files = next(args for n, args, _ in locations["/"] if n == "try_files")
        assert try_files[-1] == "/index.html", (name, try_files)
        assets = next(v for k, v in locations.items() if k.startswith("~*") and "js" in k)
        assert "immutable" in _effective_headers(server, assets).get("Cache-Control", "")


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_every_location_keeps_the_security_headers(name: str) -> None:
    for server in _servers(parse(CONFIGS[name])):
        for location, children in _locations(server).items():
            missing = SECURITY_HEADERS - set(_effective_headers(server, children))
            assert not missing, f"{name} location {location} loses {sorted(missing)}"


def _hsts_max_age(value: str) -> int:
    match = re.search(r"max-age=(\d+)", value)
    assert match, value
    return int(match.group(1))


@pytest.mark.parametrize("name", sorted(n for n in CONFIGS if not _is_homelab(n)))
def test_hsts_on_every_location_where_tls_terminates_in_front(name: str) -> None:
    for server in _servers(parse(CONFIGS[name])):
        scopes = {"server": server, **_locations(server)}
        for scope, children in scopes.items():
            headers = _effective_headers(server, children)
            value = headers.get("Strict-Transport-Security")
            assert value, f"{name} {scope}: no Strict-Transport-Security"
            assert _hsts_max_age(value) >= ONE_YEAR, (name, scope, value)
            assert "includesubdomains" not in value.lower(), (name, scope, value)


def test_the_http_only_homelab_sends_no_hsts_and_really_is_http_only() -> None:
    """HSTS is ignored over http://, but pin the premise: if the homelab ever
    serves HTTPS, this fails until HSTS is added with it."""
    homelab = [n for n in CONFIGS if _is_homelab(n)]
    assert homelab
    for name in homelab:
        for server in _servers(parse(CONFIGS[name])):
            for children in [server, *_locations(server).values()]:
                assert "Strict-Transport-Security" not in _headers(children), name
    docs = [d for d in yaml.safe_load_all(HOMELAB_INGRESS.read_text(encoding="utf-8")) if d]
    for doc in docs:
        if doc.get("kind") == "Ingress":
            assert not doc["spec"].get("tls"), "homelab ingress now has TLS: add HSTS"
            entry = doc["metadata"]["annotations"]["traefik.ingress.kubernetes.io/router.entrypoints"]
            assert entry == "web", entry


def _routes() -> list[tuple[str, dict]]:
    found = []
    for path in sorted((REPO_ROOT / "k8s").rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if "kind: Route" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if doc and doc.get("kind") == "Route":
                found.append((path.relative_to(REPO_ROOT).as_posix(), doc))
    return found


def test_every_tls_route_sends_hsts() -> None:
    routes = _routes()
    assert len(routes) >= 6, routes
    for rel, route in routes:
        if not route["spec"].get("tls"):
            continue
        annotations = route["metadata"].get("annotations") or {}
        value = annotations.get("haproxy.router.openshift.io/hsts_header")
        assert value, f"{rel} Route {route['metadata']['name']}: no hsts_header"
        assert _hsts_max_age(value) >= ONE_YEAR, (rel, value)


def test_the_parser_sees_add_header_inheritance() -> None:
    """Self-test: a location with its own add_header inherits nothing."""
    server = _servers(parse(
        'server { add_header A "1"; location / { } location /x { add_header B "2"; } }'
    ))[0]
    locations = _locations(server)
    assert _effective_headers(server, locations["/"]) == {"A": "1"}
    assert _effective_headers(server, locations["/x"]) == {"B": "2"}
    # An envsubst placeholder is a value, not a block.
    templated = _servers(parse("server { location /api/ { proxy_pass http://${UP}; } }"))[0]
    assert _locations(templated)["/api/"] == [("proxy_pass", ["http://envsubst-UP"], None)]
