"""The homelab deploy prunes old TestLookup images from the registry, and only those.

On 2026-10-18 the in-cluster registry held ~570 builds of each TestLookup image
(48.8 GB on one node's disk): every deploy pushes a new immutable
``build-YYYYMMDD-HHMMSS`` tag and nothing ever removed an old one.
``homelabsetup/prune-registry.sh`` now keeps the newest N dated builds,
``latest`` and every digest a kept tag shares, deletes the rest of
testlookup/{backend,frontend,mcp}, and never reads another repository (other
apps on the cluster deploy by digest, with no tag).

These tests drive the real script against a stub registry (the Docker
Registry HTTP API subset it uses) and pin the deploy script's wiring.
"""
from __future__ import annotations

import http.server
import pathlib
import re
import shutil
import subprocess
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
PRUNE = REPO / "homelabsetup" / "prune-registry.sh"
DEPLOY = REPO / "homelabsetup" / "deploy-homelab.sh"
BASH = shutil.which("bash")
CURL = shutil.which("curl")

pytestmark = pytest.mark.skipif(
    not PRUNE.exists() or not BASH or not CURL,
    reason="needs homelabsetup/prune-registry.sh, bash and curl",
)

# repo -> {tag: digest}. backend: the three newest dated builds plus older
# ones; one OLD tag shares a digest with a kept build (must survive); one
# ad-hoc tag. frontend: `latest` points at an old build's digest (must survive).
WORLD = {
    "testlookup/backend": {
        "build-20261001-010101": "sha256:b1",
        "build-20261002-010101": "sha256:b2",
        "build-20261003-010101": "sha256:b3",
        "build-20261004-010101": "sha256:b4",
        "build-20260901-010101": "sha256:b4",  # same image as a kept build
        "fix-something-1234": "sha256:bx",
        "latest": "sha256:b4",
    },
    "testlookup/frontend": {
        "build-20261001-010101": "sha256:f1",
        "build-20261002-010101": "sha256:f2",
        "build-20261003-010101": "sha256:f3",
        "latest": "sha256:f1",
    },
    "testlookup/mcp": {},
    "otherapp/web": {"build-20200101-000000": "sha256:o1"},
}


class _Registry(http.server.BaseHTTPRequestHandler):
    deletes: list[str] = []
    paths: list[str] = []
    refuse_deletes = False

    def log_message(self, *args):  # quiet
        pass

    def _repo_and_rest(self):
        path = self.path.split("?", 1)[0]
        self.paths.append(path)
        if not path.startswith("/v2/"):
            return None, None
        body = path[len("/v2/"):]
        for repo in WORLD:
            if body.startswith(repo + "/"):
                return repo, body[len(repo) + 1:]
        return None, body

    def do_GET(self):
        repo, rest = self._repo_and_rest()
        if self.path.split("?", 1)[0] == "/v2/":
            self._send(200, b"{}")
        elif repo and rest == "tags/list":
            tags = list(WORLD[repo]) or None
            import json
            self._send(200, json.dumps({"name": repo, "tags": tags}).encode())
        else:
            self._send(404, b"{}")

    def do_HEAD(self):
        repo, rest = self._repo_and_rest()
        if repo and rest.startswith("manifests/"):
            tag = rest[len("manifests/"):]
            digest = WORLD[repo].get(tag)
            if digest:
                self.send_response(200)
                self.send_header("Docker-Content-Digest", digest)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        repo, rest = self._repo_and_rest()
        if self.refuse_deletes:
            self._send(405, b"{}")
            return
        self.deletes.append(f"{repo}@{rest[len('manifests/'):]}")
        self._send(202, b"")

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


@pytest.fixture
def registry():
    _Registry.deletes = []
    _Registry.paths = []
    _Registry.refuse_deletes = False
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _run(registry: str, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(PRUNE), f"--registry={registry}", "--no-gc", *flags],
        capture_output=True, text=True, timeout=180,
    )


def test_keeps_the_newest_builds_latest_and_shared_digests_and_deletes_only_those_repos(registry):
    result = _run(registry, "--keep=2")
    assert result.returncode == 0, result.stderr
    assert sorted(_Registry.deletes) == sorted([
        # backend: keep b3, b4 (newest two) and latest (= b4); the old tag on b4 survives.
        "testlookup/backend@sha256:b1",
        "testlookup/backend@sha256:b2",
        "testlookup/backend@sha256:bx",
        # frontend: keep f2, f3 and latest (= f1, an old build: it survives).
    ])
    assert "deleted 3 manifest(s)" in result.stdout
    # Another app's repository is never read, let alone pruned (one run: each
    # curl is a process, slow on Windows runners).
    assert not any("otherapp" in p for p in _Registry.paths)


def test_a_dry_run_deletes_nothing(registry):
    result = _run(registry, "--keep=1", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert _Registry.deletes == []
    assert "dry run: nothing deleted" in result.stdout


def test_a_registry_that_refuses_deletes_exits_3(registry):
    _Registry.refuse_deletes = True
    result = _run(registry, "--keep=1")
    assert result.returncode == 3
    assert "REGISTRY_STORAGE_DELETE_ENABLED=true" in result.stderr


@pytest.mark.parametrize("keep", ["0", "-1", "two", ""])
def test_keep_must_be_a_whole_number_of_at_least_one(registry, keep):
    result = _run(registry, f"--keep={keep}")
    assert result.returncode == 2
    assert _Registry.deletes == []


def test_an_unreachable_registry_exits_4():
    result = subprocess.run(
        [BASH, str(PRUNE), "--registry=127.0.0.1:1", "--no-gc", "--dry-run"],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 4


def test_garbage_collection_never_deletes_untagged_manifests():
    """Other apps' images are referenced by digest only; --delete-untagged would remove them."""
    code_lines = [
        line for line in PRUNE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    gc_lines = [line for line in code_lines if "garbage-collect" in line]
    assert gc_lines, "the garbage-collect call is gone"
    assert not any("--delete-untagged" in line for line in code_lines)


# ── The deploy script's wiring ──────────────────────────────────────────────


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_the_deploy_prunes_only_after_a_healthy_run_that_pushed_a_build():
    text = _deploy()
    step = text.index("Step 12 — Prune old TestLookup images")
    degraded_exit = text.index('header "Deployment DEGRADED"')
    complete = text.index('header "Deployment Complete"')
    assert degraded_exit < step < complete, "the prune must run after the DEGRADED exit and before the success banner"
    block = text[text.rindex("\nif ", 0, step):complete]
    assert '"$SKIP_BUILD" = false' in block and '"$SKIP_REGISTRY_PRUNE" = false' in block
    assert "prune-registry.sh" in block and '--keep="$KEEP_BUILDS"' in block


def test_a_prune_failure_never_fails_the_deploy():
    text = _deploy()
    step = text.index("Step 12 — Prune old TestLookup images")
    block = text[step:text.index('header "Deployment Complete"')]
    assert "|| prune_rc=$?" in block
    assert not re.search(r"^\s*(exit|error)\b", block, flags=re.M), "the prune step must only warn"


def test_the_prune_flags_are_parsed_and_documented():
    text = _deploy()
    assert "--skip-registry-prune) SKIP_REGISTRY_PRUNE=true" in text
    assert '--keep-builds=*)     KEEP_BUILDS="${arg#*=}"' in text
    assert "#   --skip-registry-prune" in text and "#   --keep-builds=N" in text
    assert "KEEP_BUILDS=3" in text
