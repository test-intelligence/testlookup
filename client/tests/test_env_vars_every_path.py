"""Every TESTLOOKUP_* variable the Python SDK maps must work on every path.

The SDK reaches the server three ways -- ``TestLookupReporter.session()``,
the pytest plugin, and ``LiveStream`` -- and each read config on its own, so
the documented variables worked on some paths and not others:

* TESTLOOKUP_UPLOAD_MODE was mapped and read by nothing.
* TESTLOOKUP_CA_CERT / TESTLOOKUP_INSECURE were read only by LiveStream, so
  the pytest plugin could not reach a server with a self-signed certificate.
* TESTLOOKUP_COMMIT / TESTLOOKUP_FRAMEWORK were read only by LiveStream, and
  TESTLOOKUP_BUILD / BRANCH / LAUNCH not at all by ``session()``.
* TESTLOOKUP_SUITE / TESTLOOKUP_RELEASE never reached a pytest run, and
  LiveStream ignored TESTLOOKUP_RELEASE.

``ENV_EFFECTS`` declares what each variable does. A variable added to
``ConfigLoader.ENV_MAP`` without a declared, tested effect fails
``test_every_mapped_env_var_declares_its_effect``.
"""
from __future__ import annotations

import asyncio
import re
import ssl
import warnings
from pathlib import Path
from typing import Any, Callable

import certifi
import pytest

import testlookup_reporter as tr
from sdk_wire import FAKE_KEY, run_pytest_plugin, write_properties


def _run_reporter_session() -> None:
    async def go() -> None:
        reporter = tr.TestLookupReporter()
        try:
            async with reporter.session() as live:
                await live.record("test_login", "PASSED", 5)
        finally:
            await reporter.aclose()

    asyncio.run(go())


def _run_live_stream() -> None:
    async def go() -> None:
        async with tr.LiveStream(run_id="ci-run-1") as stream:
            await stream.record("test_login", "PASSED", 5)

    asyncio.run(go())


#: path name → (runner, the baseline testlookup.properties it needs). The
#: baseline lives in the file because a file is the lowest-precedence source:
#: any variable under test outranks it.
PATHS: dict[str, tuple[Callable[[], Any], dict[str, str]]] = {
    "reporter-session": (
        _run_reporter_session,
        {"endpoint": "http://file.test", "token": "jwt-from-file", "project": "proj-file"},
    ),
    "pytest-plugin": (
        run_pytest_plugin,
        {"endpoint": "http://file.test", "token": "jwt-from-file", "project": "proj-file"},
    ),
    "live-stream": (
        _run_live_stream,
        {"endpoint": "http://file.test", "api_key": "qai_from_file_not_a_key"},
    ),
}

#: /stream/ingest takes only an API key and derives the project from it, so
#: a token or a project has nothing to change on the LiveStream path.
NOT_APPLICABLE = {
    ("live-stream", "TESTLOOKUP_TOKEN"),
    ("live-stream", "TESTLOOKUP_PROJECT_ID"),
    ("live-stream", "TESTLOOKUP_PROJECT"),
}


def _ca_bundle(tmp_path: Path) -> str:
    """A one-certificate CA bundle (the first public root in certifi)."""
    pem = Path(certifi.where()).read_text(encoding="ascii")
    first = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\n", pem, re.S)
    assert first is not None
    path = tmp_path / "testlookup-ca.crt"
    path.write_bytes(first.group(0).encode("ascii"))
    return str(path)


def _trusts_only(bundle: str) -> Callable[[Any], bool]:
    def check(verify: Any) -> bool:
        if isinstance(verify, ssl.SSLContext):
            return len(verify.get_ca_certs()) == 1
        return verify == bundle

    return check


#: A digest is hex by definition (``sha256:<64 hex>``); not a secret sentinel.
_IMAGE_DIGEST = "sha256:" + "0f1e2d3c4b5a6978" * 4
_LOCKFILE_BYTES = b"[[package]]\nname = \"rootsentinel\"\n"


def _lockfile_root(tmp_path: Path) -> str:
    """A directory OTHER than the working one, holding a poetry.lock."""
    root = tmp_path / "lockfile-root"
    root.mkdir()
    (root / "poetry.lock").write_bytes(_LOCKFILE_BYTES)
    return str(root)


def _hashes_the_root_lockfile(ctx: Any) -> bool:
    import hashlib

    fingerprints = (ctx or {}).get("dependency_fingerprints") or {}
    return fingerprints == {"poetry.lock": hashlib.sha256(_LOCKFILE_BYTES).hexdigest()}


#: env var → (what it changes in ``Wire.told()``, value to set, expected).
#: ``expected`` may be a predicate. ``None`` as the value means "computed".
ENV_EFFECTS: dict[str, tuple[str, Any, Any]] = {
    "TESTLOOKUP_URL":          ("url", "http://env-url.test", "http://env-url.test"),
    "TESTLOOKUP_ENDPOINT":     ("url", "http://env-endpoint.test", "http://env-endpoint.test"),
    "TESTLOOKUP_TOKEN":        ("auth", "jwt-from-env", ("Bearer", "jwt-from-env")),
    "TESTLOOKUP_API_KEY":      ("auth", FAKE_KEY, ("X-API-Key", FAKE_KEY)),
    "TESTLOOKUP_PROJECT_ID":   ("project_id", "proj-env-id", "proj-env-id"),
    "TESTLOOKUP_PROJECT":      ("project_id", "proj-env", "proj-env"),
    "TESTLOOKUP_BUILD":        ("build_number", "build-env", "build-env"),
    "TESTLOOKUP_BRANCH":       ("branch", "branch-env", "branch-env"),
    "TESTLOOKUP_COMMIT":       ("commit_hash", "c0ffee1", "c0ffee1"),
    "TESTLOOKUP_LAUNCH":       ("launch_name", "Launch env", "Launch env"),
    "TESTLOOKUP_SUITE":        ("suite_name", "Suite env", "Suite env"),
    "TESTLOOKUP_RELEASE":      ("release_name", "2026.10-env", "2026.10-env"),
    "TESTLOOKUP_FRAMEWORK":    ("framework", "behave", "behave"),
    "TESTLOOKUP_CA_CERT":      ("verify", None, None),
    "TESTLOOKUP_INSECURE":     ("verify", "true", False),
    "TESTLOOKUP_CI_PROVIDER":  ("ci_provider", "jenkins", "jenkins"),
    "TESTLOOKUP_CI_REPO":      ("ci_repo", "acme/widgets", "acme/widgets"),
    "TESTLOOKUP_PR_NUMBER":    ("pr_number", "421", 421),
    "TESTLOOKUP_CI_ACTOR":     ("ci_actor", "octocat", "octocat"),
    "TESTLOOKUP_CI_RUN_URL":   ("ci_run_url", "https://ci.test/run/1", "https://ci.test/run/1"),
    "TESTLOOKUP_COMMIT_RANGE": ("commit_range_enabled", "0", False),
    "TESTLOOKUP_COMMIT_RANGE_BASE": ("commit_range_base", "origin/release", "origin/release"),
    # Run context (W23b FAIL-13): the typed ``run_context`` field each path sends.
    "TESTLOOKUP_RUN_CONTEXT":  ("run_context", "0", None),
    "TESTLOOKUP_RUN_CONTEXT_ROOT": ("run_context", None, None),
    "TESTLOOKUP_RUNTIME":      ("run_context", "java=21.0.2", lambda ctx: (ctx or {}).get("runtime", {}).get("java") == "21.0.2"),
    "TESTLOOKUP_IMAGE_DIGESTS": (
        "run_context", "app=" + _IMAGE_DIGEST,
        lambda ctx: (ctx or {}).get("image_digests") == {"app": _IMAGE_DIGEST},
    ),
}


def test_every_mapped_env_var_declares_its_effect():
    assert set(ENV_EFFECTS) == set(tr.ConfigLoader.ENV_MAP), (
        "every variable in ConfigLoader.ENV_MAP needs an effect the SDK "
        "really has -- or it should not be mapped (or documented) at all"
    )


@pytest.mark.parametrize(
    ("path_name", "env_var"),
    [(p, v) for v in sorted(ENV_EFFECTS) for p in sorted(PATHS) if (p, v) not in NOT_APPLICABLE],
)
def test_env_var_reaches_the_server_on_every_path(wire, monkeypatch, tmp_path, path_name, env_var):
    run, baseline = PATHS[path_name]
    write_properties(Path("testlookup.properties"), **baseline)
    observed_key, value, expected = ENV_EFFECTS[env_var]
    if env_var == "TESTLOOKUP_CA_CERT":
        value = _ca_bundle(tmp_path)
        expected = _trusts_only(value)
    if env_var == "TESTLOOKUP_RUN_CONTEXT_ROOT":
        value = _lockfile_root(tmp_path)
        expected = _hashes_the_root_lockfile
    monkeypatch.setenv(env_var, value)

    run()

    observed = wire.told().get(observed_key)
    if callable(expected):
        assert expected(observed), f"{env_var} had no effect on {path_name}: {observed!r}"
    else:
        assert observed == expected, f"{env_var} had no effect on {path_name}"


@pytest.mark.parametrize("path_name", sorted(PATHS))
def test_the_file_alone_configures_every_path(wire, path_name):
    """The baseline the matrix overrides really is in force -- otherwise an
    'override' would pass by accident."""
    run, baseline = PATHS[path_name]
    write_properties(Path("testlookup.properties"), **baseline)

    run()

    told = wire.told()
    assert told["url"] == "http://file.test"
    assert told["verify"] is True
    if path_name == "live-stream":
        assert told["auth"] == ("X-API-Key", "qai_from_file_not_a_key")
    else:
        assert told["auth"] == ("Bearer", "jwt-from-file")
        assert told["project_id"] == "proj-file"


@pytest.mark.parametrize("path_name", sorted(PATHS))
def test_a_ca_bundle_never_reaches_httpx_as_a_string(wire, monkeypatch, tmp_path, path_name):
    """httpx 0.28 deprecates verify=<str>; under ``-W error`` (or
    filterwarnings=error) that warning failed the run before the first test."""
    run, baseline = PATHS[path_name]
    write_properties(Path("testlookup.properties"), **baseline)
    monkeypatch.setenv("TESTLOOKUP_CA_CERT", _ca_bundle(tmp_path))

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        run()

    assert isinstance(wire.told()["verify"], ssl.SSLContext)


def test_upload_mode_is_not_mapped():
    """It never did anything; mapping it only made the docs promise it."""
    assert "TESTLOOKUP_UPLOAD_MODE" not in tr.ConfigLoader.ENV_MAP


def test_module_version_matches_pyproject():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert declared is not None
    assert tr.__version__ == declared.group(1)
