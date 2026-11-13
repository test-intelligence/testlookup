"""The app never runs the chromadb code its open advisories are about.

chromadb 1.5.9 is the newest release, and every release up to it carries
GHSA-f4j7-r4q5-qw2c, GHSA-36p7-vc44-83pf (critical), GHSA-2wm9-hf6c-p5cr and
GHSA-xph7-9rjv-w5fr (high). They sit in chromadb's Python HTTP server and its
auth providers. The app uses only the client: ``HttpClient`` to the Rust
``chromadb/chroma`` image and an in-process ``EphemeralClient`` with no server
and no auth (backend/requirements-ragas.txt and the CHANGELOG say so), and the
Dependabot alerts are dismissed as "vulnerable code not used" on that basis
(OSS review 2026-11-12, H-7).

That reasoning holds only while it stays true. These tests fail the day app
code imports the server, its FastAPI app or an auth provider, names one in a
string (``importlib.import_module``, a settings value), or passes
``trust_remote_code=True`` to a loader.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
APP_CODE = [ROOT / "backend" / "app", ROOT / "mcp"]
# chromadb's server, its FastAPI app and its auth providers.
UNSAFE = ("chromadb.server", "chromadb.app", "chromadb.auth")
_UNSAFE_TEXT = re.compile(r"\bchromadb\.(server|app|auth)\b")


def _sources():
    files = [p for base in APP_CODE for p in base.rglob("*.py") if "tests" not in p.parts]
    assert len(files) > 100, f"expected the app's Python sources, found {len(files)}"
    return files


def _unsafe(module: str) -> bool:
    return any(module == name or module.startswith(name + ".") for name in UNSAFE)


def test_no_app_code_imports_chromadb_server_or_auth_code():
    found = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _UNSAFE_TEXT.search(node.value):
                names = [node.value]
            if any(_unsafe(name) or _UNSAFE_TEXT.search(name) for name in names):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not found, (
        "chromadb's server and auth code carry unfixed critical advisories that are dismissed as "
        "not used; using it makes the dismissal false: " + ", ".join(found)
    )


def test_no_app_code_trusts_remote_model_code():
    found = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "trust_remote_code":
                if not (isinstance(node.value, ast.Constant) and node.value.value is False):
                    found.append(f"{path.relative_to(ROOT)}:{node.value.lineno}")
    assert not found, "trust_remote_code runs code downloaded with a model: " + ", ".join(found)


def test_the_check_sees_an_unsafe_import():
    """The scan is not vacuous: each form it refuses is recognised."""
    for snippet in ("import chromadb.server.fastapi", "from chromadb.auth import token_authn",
                    "from chromadb import auth", "x = 'chromadb.auth.token_authn.TokenAuthServerProvider'"):
        tree = ast.parse(snippet)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hits += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                hits += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                hits.append(node.value)
        assert any(_unsafe(h) or _UNSAFE_TEXT.search(h) for h in hits), snippet
    assert not _unsafe("chromadb.config") and not _UNSAFE_TEXT.search("chromadb.utils.embedding_functions")
