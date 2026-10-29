"""The Python SDK's run context (W23b FAIL-13): ``client/run_context.py`` and its wiring.

The collector runs every case of the shared vectors (``sdk-vectors/run_context.json``)
and must build EXACTLY the object the server accepts unchanged; it reads one root,
regular files only, never raises, and every SDK path -- ``TestLookupReporter``, the
pytest plugin, ``LiveStream`` -- sends it as the typed ``run_context`` field, retrying
ONCE without it when the server's 422 blames it (results are never lost to metadata).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

import run_context as rc
import testlookup_reporter as tr
from sdk_wire import FAKE_KEY, run_pytest_plugin, write_properties

_VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "sdk-vectors" / "run_context.json").read_text(encoding="utf-8")
)
_DETECTED = set(_VECTORS["detected_runtime_keys"]["python_sdk"])
_LOCK = b"[[package]]\nname = \"requests\"\nversion = \"2.32.3\"\n"


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # bytes EXACTLY as given (a \r\n stays)


def _without_detected(ctx: dict[str, Any], expect: dict[str, Any]) -> dict[str, Any]:
    """The vectors' rule: detected runtime keys the case does not name are not compared."""
    out = dict(ctx)
    runtime = {k: v for k, v in (ctx.get("runtime") or {}).items()
               if k not in _DETECTED or k in (expect.get("runtime") or {})}
    out.pop("runtime", None)
    if runtime:
        out["runtime"] = runtime
    return out


# ── The shared vectors ───────────────────────────────────────────────────────


@pytest.mark.parametrize("case", _VECTORS["cases"], ids=lambda c: c["name"])
def test_every_vector_case_builds_exactly_the_expected_context(tmp_path, case):
    _write_files(tmp_path, case["files"])
    built = rc.collect_run_context(root=str(tmp_path), env=dict(case["env"]))
    assert built is not None
    assert _without_detected(built, case["expect"]) == case["expect"]


def test_the_vocabulary_matches_the_vector_file():
    assert list(rc.ENV_ALLOWLIST) == _VECTORS["env_allowlist"]
    assert list(rc.LOCKFILE_NAMES) == _VECTORS["lockfile_names"]
    assert list(rc.TOKEN_SHAPES) == _VECTORS["token_shapes"]
    assert rc.USERINFO_RE.pattern == _VECTORS["userinfo_rule"]["pattern"]
    assert rc.REDACTED == _VECTORS["redacted"]
    assert (rc.CONTEXT_MAX_BYTES, rc.RUNTIME_MAX, rc.IMAGES_MAX, rc.ENV_VALUE_MAX) == (
        _VECTORS["caps"]["bytes"], _VECTORS["caps"]["runtime"], _VECTORS["caps"]["images"],
        _VECTORS["caps"]["env_value"],
    )
    patterns = _VECTORS["patterns"]
    assert rc.RUNTIME_KEY_RE.pattern == patterns["runtime_key"]
    assert rc.VERSION_RE.pattern == patterns["version"]
    assert rc.IMAGE_NAME_RE.pattern == patterns["image_name"]
    assert rc.DIGEST_RE.pattern == patterns["digest"]


def test_the_detected_runtime_names_this_interpreter():
    runtime = rc.collect_run_context(root=os.devnull, env={})["runtime"]
    import platform

    assert runtime["python"] == platform.python_version()
    assert runtime["implementation"] == platform.python_implementation().lower()
    assert runtime["testlookup-reporter"] == tr.__version__ == "1.2.0"
    assert runtime["pytest"] == pytest.__version__
    assert all(rc.VERSION_RE.match(v) for v in runtime.values())


def test_testlookup_runtime_beats_detection_and_the_explicit_value_beats_both():
    env = {"TESTLOOKUP_RUNTIME": "python=3.11.8"}
    assert rc.collect_run_context(root=os.devnull, env=env)["runtime"]["python"] == "3.11.8"
    explicit = rc.collect_run_context(root=os.devnull, env=env, runtime={"python": "3.11.9"})
    assert explicit["runtime"]["python"] == "3.11.9"


# ── The root, the file rules ─────────────────────────────────────────────────


def test_the_root_is_explicit_then_the_env_variable_then_config_then_cwd(tmp_path, monkeypatch):
    for name in ("explicit", "env", "config", "cwd"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "poetry.lock").write_bytes(name.encode())
    monkeypatch.chdir(tmp_path / "cwd")
    digest = {name: hashlib.sha256(name.encode()).hexdigest() for name in ("explicit", "env", "config", "cwd")}

    def lock(**kwargs: Any) -> str:
        return rc.collect_run_context(**kwargs)["dependency_fingerprints"]["poetry.lock"]

    env = {"TESTLOOKUP_RUN_CONTEXT_ROOT": str(tmp_path / "env")}
    config = {"run_context_root": str(tmp_path / "config")}
    assert lock(env={}) == digest["cwd"]
    assert lock(env={}, overrides=config) == digest["config"]
    assert lock(env=env, overrides=config) == digest["env"]
    assert lock(env=env, overrides=config, root=str(tmp_path / "explicit")) == digest["explicit"]


def test_only_regular_files_up_to_the_cap_are_hashed(tmp_path, monkeypatch):
    (tmp_path / "poetry.lock").mkdir()  # a directory named like a lockfile
    (tmp_path / "yarn.lock").write_bytes(b"x" * 11)
    (tmp_path / "go.sum").write_bytes(b"x" * 10)
    monkeypatch.setattr(rc, "LOCKFILE_MAX_BYTES", 10)
    fingerprints = rc.dependency_fingerprints(str(tmp_path))
    assert fingerprints == {"go.sum": hashlib.sha256(b"x" * 10).hexdigest()}


def test_a_symlinked_lockfile_is_not_followed(tmp_path):
    outside = tmp_path / "outside.lock"
    outside.write_bytes(_LOCK)
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.symlink(outside, root / "poetry.lock")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert rc.dependency_fingerprints(str(root)) == {}


def test_the_50_mb_cap_is_the_default():
    assert rc.LOCKFILE_MAX_BYTES == 50 * 1024 * 1024


# ── Off, never raises, fits ──────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
def test_the_env_variable_turns_collection_off(value):
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": value}) is None


def test_explicit_beats_env_beats_config_for_enabled():
    assert rc.collect_run_context(env={}, overrides={"collect_run_context": "false"}) is None
    on = rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": "1"}, overrides={"collect_run_context": "false"})
    assert on is not None
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": "1"}, enabled=False) is None
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": "0"}, enabled=True) is not None


def test_it_never_raises(monkeypatch):
    def boom() -> dict[str, str]:
        raise RuntimeError("interpreter probe failed")

    monkeypatch.setattr(rc, "detect_runtime", boom)
    assert rc.collect_run_context(env={}) is None


def test_an_unreadable_lockfile_is_skipped_not_fatal(tmp_path, monkeypatch):
    (tmp_path / "poetry.lock").write_bytes(_LOCK)
    (tmp_path / "go.sum").write_bytes(_LOCK)
    real_hash = rc._hash_file

    def flaky(path: str):
        if path.endswith("poetry.lock"):
            raise PermissionError("locked by another process")
        return real_hash(path)

    monkeypatch.setattr(rc, "_hash_file", flaky)
    assert set(rc.collect_run_context(root=str(tmp_path), env={})["dependency_fingerprints"]) == {"go.sum"}


def test_nothing_collected_sends_nothing(monkeypatch):
    monkeypatch.setattr(rc, "detect_runtime", lambda: {})
    assert rc.collect_run_context(root=os.devnull, env={}) is None


def test_an_over_cap_context_drops_env_then_images_then_runtime_extras(tmp_path):
    for name in rc.LOCKFILE_NAMES:
        (tmp_path / name).write_bytes(name.encode())
    images = ",".join(f"img{i}/{'a' * 195}:{'t' * 128}=sha256:{'0f' * 32}" for i in range(8))
    env = {key: ("label text " * 19)[:200] for key in rc.ENV_ALLOWLIST}
    runtime = ",".join(f"tool{i:02d}=1.{'0' * 60}" for i in range(11))
    full_env = {**env, "TESTLOOKUP_IMAGE_DIGESTS": images, "TESTLOOKUP_RUNTIME": runtime}
    uncapped = {
        "v": 1, "runtime": rc._merge_runtime(runtime, rc.detect_runtime()),
        "dependency_fingerprints": rc.dependency_fingerprints(str(tmp_path)),
        "image_digests": rc._merge_images(images), "env": rc.allowlisted_env(env),
    }
    assert rc._size(uncapped) > rc.CONTEXT_MAX_BYTES  # the case really is over the cap
    built = rc.collect_run_context(root=str(tmp_path), env=full_env)
    assert rc._size(built) <= rc.CONTEXT_MAX_BYTES
    assert "env" not in built and len(built["image_digests"]) == 8  # env went first, and was enough
    assert len(built["dependency_fingerprints"]) == len(rc.LOCKFILE_NAMES)
    assert {"python", "implementation", "os"} <= set(built["runtime"])


def test_runtime_extras_go_last_and_the_detected_keys_stay(monkeypatch):
    monkeypatch.setattr(rc, "CONTEXT_MAX_BYTES", 600)
    ctx = {"v": 1, "runtime": {"python": "3.11.9", **{f"tool{i:02d}": "1." + "0" * 60 for i in range(15)}},
           "env": {"TZ": "x" * 200}, "image_digests": {"app": "sha256:" + "0f" * 32}}
    fitted = rc._fit(ctx, keep_runtime=("python",))
    assert "env" not in fitted and "image_digests" not in fitted
    assert fitted["runtime"]["python"] == "3.11.9"
    assert 1 < len(fitted["runtime"]) < 16 and rc._size(fitted) <= 600
    # The extras go newest-last first: the earliest ones are the ones kept.
    assert "tool00" in fitted["runtime"] and "tool14" not in fitted["runtime"]


# ── The three SDK paths (the ``wire`` fake server) ──────────────────────────

_FILE = {"endpoint": "http://file.test", "token": "jwt-from-file", "project": "proj-file"}
_STREAM_FILE = {"endpoint": "http://file.test", "api_key": FAKE_KEY}


def _reporter_session(**reporter_kwargs: Any) -> None:
    async def go() -> None:
        reporter = tr.TestLookupReporter(**reporter_kwargs)
        try:
            async with reporter.session() as live:
                await live.record("test_login", "PASSED", 5)
        finally:
            await reporter.aclose()

    asyncio.run(go())


def _live_stream(**kwargs: Any) -> None:
    async def go() -> None:
        async with tr.LiveStream(run_id="ci-run-1", **kwargs) as stream:
            await stream.record("test_login", "PASSED", 5)

    asyncio.run(go())


def _session_bodies(wire) -> list[dict[str, Any]]:
    return wire.bodies("POST", "/api/v1/stream/sessions")


def _ingest_metas(wire) -> list[dict[str, Any]]:
    return [body.get("meta") or {} for body in wire.bodies("POST", "/api/v1/stream/ingest")]


def _expected_lock() -> str:
    return hashlib.sha256(_LOCK).hexdigest()


def test_the_reporter_sends_the_context_on_session_create(wire, monkeypatch):
    write_properties(Path("testlookup.properties"), **_FILE)
    Path("poetry.lock").write_bytes(_LOCK)
    monkeypatch.setenv("BUILD_IMAGE", "ghp_" + "Sentinel" * 5)
    monkeypatch.setenv("TESTLOOKUP_RUNTIME", "python=3.11.8")
    _reporter_session()
    (body,) = _session_bodies(wire)
    ctx = body["run_context"]
    assert ctx["dependency_fingerprints"] == {"poetry.lock": _expected_lock()}
    assert ctx["runtime"]["python"] == "3.11.8"
    assert ctx["env"]["BUILD_IMAGE"] == rc.REDACTED
    assert "Sentinel" not in json.dumps(body)


def test_the_pytest_plugin_sends_it_too(wire):
    write_properties(Path("testlookup.properties"), **_FILE)
    Path("poetry.lock").write_bytes(_LOCK)
    run_pytest_plugin()
    (body,) = _session_bodies(wire)
    assert body["run_context"]["dependency_fingerprints"] == {"poetry.lock": _expected_lock()}
    assert body["run_context"]["runtime"]["pytest"] == pytest.__version__


def test_live_stream_sends_it_as_a_typed_meta_field(wire):
    write_properties(Path("testlookup.properties"), **_STREAM_FILE)
    Path("poetry.lock").write_bytes(_LOCK)
    _live_stream(metadata={"team": "payments"})
    meta = _ingest_metas(wire)[0]
    assert meta["run_context"]["dependency_fingerprints"] == {"poetry.lock": _expected_lock()}
    assert "run_context" not in (meta.get("metadata") or {})


def test_the_constructor_args_pick_the_root_and_turn_it_off(wire, tmp_path):
    write_properties(Path("testlookup.properties"), **_FILE)
    elsewhere = tmp_path / "service"
    elsewhere.mkdir()
    (elsewhere / "go.sum").write_bytes(_LOCK)
    _reporter_session(run_context_root=str(elsewhere))
    _reporter_session(collect_run_context=False)
    first, second = _session_bodies(wire)
    assert first["run_context"]["dependency_fingerprints"] == {"go.sum": _expected_lock()}
    assert "run_context" not in second


def test_the_config_file_turns_it_off(wire):
    write_properties(Path("testlookup.properties"), **_FILE, run_context="false")
    _reporter_session()
    assert "run_context" not in _session_bodies(wire)[0]


def _refuse_first(wire, path: str, loc: list[str]) -> list[int]:
    """Make the fake answer the first POST to ``path`` carrying a context with a 422."""
    original = wire.handle
    refused: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == path and not refused:
            body = json.loads(request.content)
            if "run_context" in body or "run_context" in (body.get("meta") or {}):
                wire.requests.append(request)
                refused.append(1)
                return httpx.Response(422, json={"detail": [{"loc": loc, "msg": "Value error, x", "type": "value_error"}]})
        return original(request)

    wire.handle = handle  # read when the SDK builds its client
    return refused


def test_the_reporter_retries_once_without_the_context_on_a_422_that_blames_it(wire):
    write_properties(Path("testlookup.properties"), **_FILE)
    refused = _refuse_first(wire, "/api/v1/stream/sessions", ["body", "run_context", "env"])
    _reporter_session()
    first, second = _session_bodies(wire)
    assert refused == [1]
    assert "run_context" in first and "run_context" not in second
    assert {k: v for k, v in first.items() if k != "run_context"} == second


def test_a_422_about_something_else_is_not_retried(wire):
    write_properties(Path("testlookup.properties"), **_FILE)
    _refuse_first(wire, "/api/v1/stream/sessions", ["body", "build_number"])
    with pytest.raises(httpx.HTTPStatusError):
        _reporter_session()
    assert len(_session_bodies(wire)) == 1


def test_live_stream_retries_once_without_the_context(wire):
    write_properties(Path("testlookup.properties"), **_STREAM_FILE)
    refused = _refuse_first(wire, "/api/v1/stream/ingest", ["body", "meta", "run_context", "env"])
    _live_stream()
    metas = _ingest_metas(wire)
    assert refused == [1]
    assert "run_context" in metas[0]
    assert all("run_context" not in meta for meta in metas[1:]) and len(metas) >= 2


def test_locates_run_context_reads_both_422_shapes():
    assert rc.locates_run_context({"detail": [{"loc": ["body", "run_context", "env"], "msg": "x"}]})
    assert rc.locates_run_context({"detail": [{"loc": ["body", "meta", "run_context"], "msg": "x"}]})
    assert rc.locates_run_context({"detail": {"code": "invalid_run_context", "message": "x"}})
    assert rc.locates_run_context({"detail": {"code": "bad_json", "message": "x"}})
    assert not rc.locates_run_context({"detail": [{"loc": ["body", "build_number"], "msg": "x"}]})
    assert not rc.locates_run_context({"detail": "Not authenticated"})
    assert not rc.locates_run_context(["not", "a", "mapping"])
