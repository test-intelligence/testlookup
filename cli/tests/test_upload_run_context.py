"""The CLI's run context (W23b FAIL-13): ``testlookup_cli/run_context.py`` and the upload flags.

A deliberate copy of the SDK collector WITHOUT interpreter detection (D-13: the CLI's
Python is not the tests' runtime). It runs every case of ``sdk-vectors/run_context.json``,
and ``testlookup upload`` sends it as the ``run_context`` form field with the six W23b
flags, retrying ONCE without it when the server's 422 blames it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from testlookup_cli import __version__
from testlookup_cli import run_context as rc
from testlookup_cli.app import app
from testlookup_cli.commands import upload

runner = CliRunner()

_VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "sdk-vectors" / "run_context.json").read_text(encoding="utf-8")
)
_DETECTED = set(_VECTORS["detected_runtime_keys"]["cli"])
_LOCK = b"[[package]]\nname = \"requests\"\nversion = \"2.32.3\"\n"
_DIGEST = "sha256:" + "0f1e2d3c4b5a6978" * 4


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _without_detected(ctx: dict[str, Any], expect: dict[str, Any]) -> dict[str, Any]:
    out = dict(ctx)
    runtime = {k: v for k, v in (ctx.get("runtime") or {}).items()
               if k not in _DETECTED or k in (expect.get("runtime") or {})}
    out.pop("runtime", None)
    if runtime:
        out["runtime"] = runtime
    return out


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No TESTLOOKUP_* or allowlisted variable from the machine leaks in; cwd is empty."""
    for key in list(os.environ):
        if key.startswith("TESTLOOKUP_") or key in rc.ENV_ALLOWLIST:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── The collector (the deliberate copy) ──────────────────────────────────────


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
    caps = _VECTORS["caps"]
    assert (rc.CONTEXT_MAX_BYTES, rc.RUNTIME_MAX, rc.IMAGES_MAX, rc.ENV_VALUE_MAX) == (
        caps["bytes"], caps["runtime"], caps["images"], caps["env_value"],
    )


def test_the_cli_records_no_interpreter_only_its_own_version(clean_env):
    runtime = rc.collect_run_context(env={})["runtime"]
    assert runtime == {"testlookup-cli": __version__} == {"testlookup-cli": "0.3.1"}


def test_runtime_flags_beat_the_variable_and_bad_entries_are_dropped(clean_env):
    built = rc.collect_run_context(
        env={"TESTLOOKUP_RUNTIME": "java=17.0.1,node=20.11.1"},
        runtime=["java=21.0.2", "Bad Key=1", "noequals"],
    )
    assert built["runtime"] == {"java": "21.0.2", "node": "20.11.1", "testlookup-cli": __version__}
    assert rc.rejected_entries(["java=21.0.2", "Bad Key=1", "noequals"], "runtime") == ["Bad Key=1", "noequals"]
    assert rc.rejected_entries([f"app={_DIGEST}", "app=sha256:short"], "image") == ["app=sha256:short"]
    assert rc.rejected_entries(None, "image") == []


def test_image_flags_beat_the_variable_whole(clean_env):
    env = {"TESTLOOKUP_IMAGE_DIGESTS": f"db={_DIGEST}"}
    assert rc.collect_run_context(env=env)["image_digests"] == {"db": _DIGEST}
    assert rc.collect_run_context(env=env, image_digests=[f"app={_DIGEST}"])["image_digests"] == {"app": _DIGEST}


def test_the_root_is_the_flag_then_the_variable_then_cwd(clean_env):
    for name in ("flag", "env"):
        (clean_env / name).mkdir()
        (clean_env / name / "go.sum").write_bytes(name.encode())
    (clean_env / "go.sum").write_bytes(b"cwd")

    def lock(**kwargs: Any) -> str:
        return rc.collect_run_context(**kwargs)["dependency_fingerprints"]["go.sum"]

    env = {"TESTLOOKUP_RUN_CONTEXT_ROOT": str(clean_env / "env")}
    assert lock(env={}) == hashlib.sha256(b"cwd").hexdigest()
    assert lock(env=env) == hashlib.sha256(b"env").hexdigest()
    assert lock(env=env, root=str(clean_env / "flag")) == hashlib.sha256(b"flag").hexdigest()
    assert lock(env={}, overrides={"run_context_root": str(clean_env / "env")}) == hashlib.sha256(b"env").hexdigest()


def test_only_regular_files_within_the_cap(tmp_path, monkeypatch):
    (tmp_path / "poetry.lock").mkdir()
    (tmp_path / "yarn.lock").write_bytes(b"x" * 11)
    (tmp_path / "go.sum").write_bytes(b"x" * 10)
    monkeypatch.setattr(rc, "LOCKFILE_MAX_BYTES", 10)
    assert rc.dependency_fingerprints(str(tmp_path)) == {"go.sum": hashlib.sha256(b"x" * 10).hexdigest()}
    assert rc.dependency_fingerprints(str(tmp_path / "missing")) == {}


def test_a_file_that_grows_past_the_cap_while_read_is_dropped(tmp_path, monkeypatch):
    (tmp_path / "go.sum").write_bytes(b"x" * 10)
    real_lstat = os.lstat
    monkeypatch.setattr(rc.os, "lstat", lambda p: SimpleNamespace(st_mode=real_lstat(p).st_mode, st_size=1))
    monkeypatch.setattr(rc, "LOCKFILE_MAX_BYTES", 5)
    monkeypatch.setattr(rc, "_CHUNK", 4)
    assert rc.dependency_fingerprints(str(tmp_path)) == {}


def test_an_unreadable_lockfile_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "go.sum").write_bytes(_LOCK)

    def denied(_path: str):
        raise PermissionError("locked")

    monkeypatch.setattr(rc, "_hash_file", denied)
    assert rc.dependency_fingerprints(str(tmp_path)) == {}


def test_secrets_are_redacted_before_sending():
    assert rc.redact_env_value("ghp_" + "Sentinel" * 5) == rc.REDACTED
    assert rc.redact_env_value("https://deploy:Hunter2Sentinel@registry.example.com") == (
        "https://deploy:[REDACTED]@registry.example.com"
    )
    assert rc.redact_env_value("Europe/\tBerlin") == "Europe/Berlin"
    assert rc.redact_env_value("word " * 60) == ("word " * 60)[:200]
    env = rc.allowlisted_env({"BUILD_IMAGE": "glpat-" + "SentinelSent1234", "HOME": "/home/ci", "TZ": ""})
    assert env == {"BUILD_IMAGE": rc.REDACTED}


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_the_variable_turns_it_off(value):
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": value}) is None


def test_enabled_precedence():
    assert rc.collect_run_context(env={}, enabled=False) is None
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": "0"}, enabled=True) is not None
    assert rc.collect_run_context(env={}, overrides={"collect_run_context": "no"}) is None
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": "yes"}, overrides={"collect_run_context": "no"})
    assert rc.collect_run_context(env={"TESTLOOKUP_RUN_CONTEXT": "maybe"}) is not None  # unparseable = default on
    assert rc._flag(True) is True


def test_it_never_raises_and_sends_nothing_when_nothing_was_found(monkeypatch):
    monkeypatch.setattr(rc, "detect_runtime", lambda: {})
    assert rc.collect_run_context(root=os.devnull, env={}) is None

    def boom() -> dict[str, str]:
        raise RuntimeError("probe failed")

    monkeypatch.setattr(rc, "detect_runtime", boom)
    assert rc.collect_run_context(env={}) is None


def test_an_unparseable_cli_version_records_no_runtime(monkeypatch):
    monkeypatch.setattr(rc, "__version__", "!!")
    assert rc.detect_runtime() == {}


def test_an_over_cap_context_is_fitted(monkeypatch):
    monkeypatch.setattr(rc, "CONTEXT_MAX_BYTES", 300)
    env = {"TZ": "x y " * 50, "TESTLOOKUP_IMAGE_DIGESTS": f"app={_DIGEST}",
           "TESTLOOKUP_RUNTIME": ",".join(f"tool{i}=1.{'0' * 40}" for i in range(6))}
    built = rc.collect_run_context(root=os.devnull, env=env)
    assert "env" not in built and "image_digests" not in built
    assert built["runtime"]["testlookup-cli"] == __version__ and rc._size(built) <= 300
    capped = rc._merge_runtime({f"tool{i:02d}": "1.0" for i in range(20)})
    assert len(capped) == rc.RUNTIME_MAX
    images = rc._merge_images(",".join(f"app{i}={_DIGEST}" for i in range(10)))
    assert len(images) == rc.IMAGES_MAX


def test_locates_run_context_reads_both_422_shapes():
    assert rc.locates_run_context({"detail": {"code": "invalid_run_context", "message": "x"}})
    assert rc.locates_run_context({"detail": {"code": "bad_json", "message": "x"}})
    assert rc.locates_run_context({"detail": [{"loc": ["body", "run_context"], "msg": "x"}]})
    assert rc.locates_run_context({"detail": [{"loc": ["body", "meta", "run_context", "env"]}]})
    assert not rc.locates_run_context({"detail": {"code": "other"}})
    assert not rc.locates_run_context({"detail": [{"loc": ["body"]}, "not-a-dict"]})
    assert not rc.locates_run_context({"detail": "text"})
    assert not rc.locates_run_context(None)


# ── The form and the flags ───────────────────────────────────────────────────


def test_the_form_carries_the_context_the_environment_and_run_ai():
    ctx = {"v": 1, "runtime": {"java": "21.0.2"}}
    form = upload._build_form_data(project="p", build="b", run_context=ctx, environment="staging", run_ai=False)
    assert json.loads(form["run_context"]) == ctx
    assert form["run_context"] == '{"v":1,"runtime":{"java":"21.0.2"}}'  # compact
    assert form["environment"] == "staging" and form["run_ai"] == "false"
    plain = upload._build_form_data(project="p", build="b")
    assert not {"run_context", "environment", "run_ai"} & set(plain)


@pytest.fixture
def report(tmp_path: Path) -> Path:
    path = tmp_path / "results.xml"
    path.write_text('<?xml version="1.0"?><testsuite name="s" tests="1"><testcase name="t"/></testsuite>',
                    encoding="utf-8")
    return path


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _fake_upload(**kwargs):
        calls.append(kwargs)
        return {"status": "accepted", "run_id": "run-1", "task_id": "task-1", "total_results": 1}

    monkeypatch.setattr(upload, "_upload_file", _fake_upload)
    monkeypatch.setattr(upload, "resolve_commit_range", lambda **_kw: None)
    return calls


def test_the_six_flags_reach_the_upload(clean_env, report, captured):
    root = clean_env / "svc"
    root.mkdir()
    (root / "poetry.lock").write_bytes(_LOCK)
    result = runner.invoke(app, [
        "upload", "file", str(report), "-p", "proj", "-b", "b1", "--run-context-root", str(root),
        "--runtime", "java=21.0.2", "--runtime", "node=20.11.1", "--image-digest", f"app={_DIGEST}",
        "--environment", "staging", "--no-ai", "-o", "json",
    ])
    assert result.exit_code == 0, result.output
    (call,) = captured
    assert call["environment"] == "staging" and call["run_ai"] is False
    ctx = call["run_context"]
    assert ctx["dependency_fingerprints"] == {"poetry.lock": hashlib.sha256(_LOCK).hexdigest()}
    assert ctx["runtime"] == {"java": "21.0.2", "node": "20.11.1", "testlookup-cli": __version__}
    assert ctx["image_digests"] == {"app": _DIGEST}


def test_no_run_context_sends_none_and_the_defaults_keep_ai_on(clean_env, report, captured):
    result = runner.invoke(app, ["upload", "file", str(report), "-p", "proj", "-b", "b1", "--no-run-context"])
    assert result.exit_code == 0, result.output
    assert captured[0]["run_context"] is None
    assert captured[0]["run_ai"] is True and captured[0]["environment"] is None


def test_a_bad_runtime_flag_is_named_and_left_out(clean_env, report, captured):
    result = runner.invoke(app, ["upload", "file", str(report), "-p", "proj", "-b", "b1",
                                 "--runtime", "Java 21", "--image-digest", "app=latest"])
    assert result.exit_code == 0, result.output
    assert "--runtime 'Java 21' ignored" in result.output
    assert "--image-digest 'app=latest' ignored" in result.output
    assert captured[0]["run_context"]["runtime"] == {"testlookup-cli": __version__}
    assert "image_digests" not in captured[0]["run_context"]


def test_upload_dir_collects_once_and_stamps_every_file(clean_env, captured, monkeypatch):
    for name in ("a.xml", "b.xml"):
        (clean_env / name).write_text("<testsuite/>", encoding="utf-8")
    seen: list[dict] = []
    real = upload.collect_run_context

    def _spy(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(upload, "collect_run_context", _spy)
    result = runner.invoke(app, ["upload", "dir", str(clean_env), "-p", "proj", "-b", "b1",
                                 "--runtime", "java=21.0.2", "--environment", "prod", "--no-ai"])
    assert result.exit_code == 0, result.output
    assert len(seen) == 1 and len(captured) == 2
    assert captured[0]["run_context"] == captured[1]["run_context"]
    assert all(c["environment"] == "prod" and c["run_ai"] is False for c in captured)


# ── The one retry without the context ───────────────────────────────────────


class _Resp:
    def __init__(self, status: int, body: Any = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers: dict[str, str] = {}
        self.text = "<html>bad gateway</html>" if isinstance(self._body, Exception) else json.dumps(self._body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.forms: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, **kwargs):
        self.forms.append(dict(kwargs["data"]))  # a snapshot: the CLI edits its form
        return self.responses.pop(0)


def _send(monkeypatch, tmp_path: Path, *responses) -> tuple[Any, _Client]:
    fake = _Client(responses)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: fake)
    monkeypatch.setattr(upload, "get_profile", lambda name=None: {"url": "http://tl.test"})
    monkeypatch.setattr(upload.client, "_build_headers", lambda profile: {})
    path = tmp_path / "r.xml"
    path.write_text("<testsuite/>", encoding="utf-8")
    ctx = {"v": 1, "runtime": {"java": "21.0.2"}}
    try:
        result: Any = asyncio.run(upload._upload_file(path=path, project="p", build="1", run_context=ctx))
    except Exception as exc:  # noqa: BLE001 - the caller asserts on it
        result = exc
    return result, fake


_ACCEPTED = {"status": "accepted", "run_id": "run-1", "task_id": "task-1", "total_results": 1}


@pytest.mark.parametrize("code", ["invalid_run_context", "bad_json"])
def test_a_422_blaming_the_context_is_retried_once_without_it(monkeypatch, tmp_path, code):
    refusal = _Resp(422, {"detail": {"code": code, "message": "run_context is not a JSON object."}})
    result, fake = _send(monkeypatch, tmp_path, refusal, _Resp(202, _ACCEPTED))
    assert result == _ACCEPTED
    assert "run_context" in fake.forms[0] and "run_context" not in fake.forms[1]
    assert {k: v for k, v in fake.forms[0].items() if k != "run_context"} == fake.forms[1]


def test_a_second_refusal_is_not_retried_again(monkeypatch, tmp_path):
    refusal = _Resp(422, {"detail": {"code": "invalid_run_context", "message": "x"}})
    other = _Resp(422, {"detail": [{"loc": ["body", "build_number"], "msg": "too long"}]})
    result, fake = _send(monkeypatch, tmp_path, refusal, other)
    assert isinstance(result, Exception) and len(fake.forms) == 2


@pytest.mark.parametrize("body", [
    {"detail": [{"loc": ["body", "build_number"], "msg": "too long"}]},
    ValueError("not json"),
])
def test_a_422_about_something_else_is_not_retried(monkeypatch, tmp_path, body):
    result, fake = _send(monkeypatch, tmp_path, _Resp(422, body))
    assert isinstance(result, Exception) and len(fake.forms) == 1
