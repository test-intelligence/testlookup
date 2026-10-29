"""W23b FAIL-13: ``schemas.RunContext`` + ``run_context_service`` (validation and redaction).

The run context arrives with an API key, so it is attacker-controlled: every key is
allowlisted or pattern-checked, every section and the whole object are capped, env
values are redacted BEFORE the object is stored anywhere. These tests drive the model
the routes use, the shared vectors (``sdk-vectors/run_context.json``: every case the
SDK builds must be accepted UNCHANGED) and REAL-LOOKING secret samples, built inside
each test (non-hex sentinels; never a real secret).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.schemas import IngestPayload, LiveSessionCreate, LiveStreamMeta, RunContext
from app.services import run_context_service as svc
from app.services.run_context_vocab import (
    CONTEXT_MAX_BYTES,
    ENV_ALLOWLIST,
    REDACTED,
    RUN_CONTEXT_ERRORS,
)

_VECTORS = json.loads(
    (Path(__file__).resolve().parents[2] / "sdk-vectors" / "run_context.json").read_text(encoding="utf-8")
)
_PROJECT = "11111111-2222-4333-8444-555555555555"
_ONE_RESULT = [{"test_name": "checkout_flow_test", "status": "PASSED"}]  # no "sentinel": that word marks a secret here


def _message(exc: ValidationError) -> str:
    return svc.refusal_sentence(exc)


def _sentence(error: str, section: str) -> str:
    """The fixed RUN_CONTEXT_ERRORS sentence, filled as the model fills it."""
    caps = {"runtime": 16, "image_digests": 8}
    return RUN_CONTEXT_ERRORS[error].format(
        keys=", ".join(ENV_ALLOWLIST), section=section, max=caps.get(section, 0),
    )


def _secret_samples() -> dict[str, str]:
    """REAL-LOOKING samples (S-4), one per allowlisted key; never hex-only."""
    seg = "SentinelSentinelSentin"  # 22 characters, non-hex
    return {
        "BUILD_IMAGE": "ghp_" + "Sentinel" * 5,
        "NODE_ENV": "github_pat_" + "SentinelSentinel_" * 3,
        "CI_RUNNER_VERSION": "glpat-" + "SentinelSent1234",
        "CI_SERVER_VERSION": "xoxb-" + "1234-Sentinel",
        "ImageOS": "AKIA" + "SENTINELSENTINEL",
        "ImageVersion": "https://deploy:Hunter2Sentinel@registry.example.com",
        "RUNNER_OS": "Bearer " + "Sentinel" * 4,
        "RUNNER_ARCH": "eyJ" + seg[:21] + ".eyJ" + seg[:21] + "." + "Sentinel" * 3,
        "LC_ALL": "token=SentinelValue1",
    }


def _secret_needles() -> list[str]:
    """The secret PART of each sample: none may survive anywhere."""
    return [
        "Sentinel" * 5,
        "SentinelSentinel_" * 3,
        "SentinelSent1234",
        "1234-Sentinel",
        "SENTINELSENTINEL",
        "Hunter2Sentinel",
        "Sentinel" * 4,
        "SentinelSentinelSentin",
        "SentinelValue1",
    ]


# ── The shared vectors ───────────────────────────────────────────────────────


@pytest.mark.parametrize("case", _VECTORS["cases"], ids=lambda c: c["name"])
def test_every_vector_case_is_accepted_unchanged(case):
    """The SDK never builds what the server would rewrite or refuse."""
    expect = case["expect"]
    assert RunContext.model_validate(expect).model_dump(exclude_none=True) == expect
    assert svc.validated_or_none(expect) == expect


@pytest.mark.parametrize("case", _VECTORS["refusals"], ids=lambda c: c["name"])
def test_every_vector_refusal_is_refused_with_its_sentence(case):
    with pytest.raises(ValidationError) as caught:
        RunContext.model_validate(case["run_context"])
    assert _message(caught.value) == case["message"] == _sentence(case["error"], case["field"])
    # Located under run_context.<field> on the JSON route.
    with pytest.raises(ValidationError) as on_route:
        IngestPayload.model_validate(
            {"project_id": _PROJECT, "build_number": "b-1", "results": _ONE_RESULT,
             "run_context": case["run_context"]}
        )
    locs = [tuple(err["loc"]) for err in on_route.value.errors()]
    assert locs and all(loc[:1] == ("run_context",) for loc in locs), locs
    assert svc.validated_or_none(case["run_context"]) is None


@pytest.mark.parametrize("case", _VECTORS["redactions"], ids=lambda c: c["name"])
def test_every_vector_redaction_is_what_the_server_stores(case):
    ctx = RunContext.model_validate({"v": 1, "env": case["env"]})
    assert ctx.model_dump(exclude_none=True)["env"] == case["stored"]


@pytest.mark.parametrize("case", _VECTORS["form_refusals"], ids=lambda c: c["name"])
def test_every_vector_form_refusal(case):
    with pytest.raises(svc.RunContextRefused) as caught:
        svc.parse_form_run_context(case["form_value"])
    assert caught.value.detail() == {"code": case["code"], "message": case["message"]}


# ── The size cap ─────────────────────────────────────────────────────────────


def _sized(target: int) -> dict[str, Any]:
    """A context whose compact UTF-8 JSON is exactly ``target`` bytes, every section valid."""
    from app.services.run_context_vocab import LOCKFILE_NAMES

    ctx: dict[str, Any] = {
        "v": 1,
        "runtime": {f"tool{i:02d}": "1." + "0" * 60 for i in range(16)},
        "dependency_fingerprints": {name: "0f" * 32 for name in LOCKFILE_NAMES},
        "image_digests": {
            f"img{i}/" + "a" * 195 + ":" + "t" * 128: "sha256:" + "0f" * 32 for i in range(8)
        },
        "env": {key: "" for key in ENV_ALLOWLIST},
    }
    filler = "label sentinel "  # letters and spaces: nothing for a redactor to rewrite

    def size() -> int:
        return len(json.dumps(ctx, separators=(",", ":")).encode("utf-8"))

    for key in ENV_ALLOWLIST:
        while size() < target and len(ctx["env"][key]) < 200:
            ctx["env"][key] += filler[len(ctx["env"][key]) % len(filler)]
    assert size() == target, size()
    return ctx


def test_a_context_of_exactly_8192_bytes_is_accepted():
    ctx = _sized(CONTEXT_MAX_BYTES)
    assert RunContext.model_validate(ctx).model_dump(exclude_none=True) == ctx


def test_a_context_of_8193_bytes_is_refused_too_large_on_every_schema():
    ctx = _sized(CONTEXT_MAX_BYTES + 1)
    with pytest.raises(ValidationError) as caught:
        RunContext.model_validate(ctx)
    assert _message(caught.value) == RUN_CONTEXT_ERRORS["too_large"]
    for model, base in (
        (IngestPayload, {"project_id": _PROJECT, "build_number": "b-1", "results": _ONE_RESULT}),
        (LiveSessionCreate, {"project_id": _PROJECT, "client_name": "ci"}),
        (LiveStreamMeta, {}),
    ):
        with pytest.raises(ValidationError) as on_model:
            model.model_validate({**base, "run_context": ctx})
        first = on_model.value.errors()[0]
        assert first["loc"][0] == "run_context"
        assert svc.refusal_sentence(on_model.value) == RUN_CONTEXT_ERRORS["too_large"]


# ── Secrets never survive ────────────────────────────────────────────────────


def test_real_looking_secret_samples_never_survive_validation():
    dump = RunContext.model_validate({"v": 1, "env": _secret_samples()}).model_dump(exclude_none=True)
    stored = json.dumps(dump)
    for needle in _secret_needles():
        assert needle not in stored
    assert "sentinel" not in stored.lower() and "hunter2" not in stored.lower()
    env = dump["env"]
    for key in ("BUILD_IMAGE", "NODE_ENV", "CI_RUNNER_VERSION", "CI_SERVER_VERSION", "ImageOS"):
        assert env[key] == REDACTED, key
    assert env["ImageVersion"] == "https://deploy:[REDACTED]@registry.example.com"
    assert env["RUNNER_OS"] == "Bearer [REDACTED]"
    assert env["RUNNER_ARCH"] == "[REDACTED_JWT]"
    assert env["LC_ALL"] == "token=[REDACTED]"


@pytest.mark.parametrize("model,base", [
    (IngestPayload, {"project_id": _PROJECT, "build_number": "b-1", "results": _ONE_RESULT}),
    (LiveSessionCreate, {"project_id": _PROJECT, "client_name": "ci"}),
    (LiveStreamMeta, {}),
])
def test_each_request_schema_stores_the_redacted_form(model, base):
    parsed = model.model_validate({**base, "run_context": {"v": 1, "env": _secret_samples()}})
    dumped = json.dumps(parsed.model_dump(mode="json"))
    assert "sentinel" not in dumped.lower() and "hunter2" not in dumped.lower()
    assert parsed.run_context is not None and parsed.run_context.env["BUILD_IMAGE"] == REDACTED


def test_redact_env_value_is_idempotent_and_leaves_plain_labels():
    for raw in [*_secret_samples().values(), "Europe/Berlin", "en_US.UTF-8", "ubuntu24"]:
        once = svc.redact_env_value(raw)
        assert svc.redact_env_value(once) == once
    assert svc.redact_env_value("Europe/Berlin") == "Europe/Berlin"
    # A short password: redact_text's own connection-string rule wants 4+ characters.
    assert svc.redact_env_value("https://u:pw@registry.example.com") == "https://u:[REDACTED]@registry.example.com"


# ── Forward compatibility, empty sections ────────────────────────────────────


def test_an_unknown_top_level_section_is_ignored_never_stored():
    raw = {"v": 1, "runtime": {"python": "3.11.9"}, "toolchain_sentinel": {"anything": "goes"}}
    assert RunContext.model_validate(raw).model_dump(exclude_none=True) == {"v": 1, "runtime": {"python": "3.11.9"}}


def test_empty_sections_are_dropped():
    raw = {"v": 1, "runtime": {}, "env": {}, "dependency_fingerprints": {"poetry.lock": "0f" * 32}}
    assert svc.validated_or_none(raw) == {"v": 1, "dependency_fingerprints": {"poetry.lock": "0f" * 32}}


# ── The service helpers ──────────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def _log(self, level: str):
        def emit(event: str, **kw: Any) -> None:
            self.lines.append((level, event, kw))
        return emit

    def __getattr__(self, level: str):
        return self._log(level)


def test_validated_or_none_drops_and_logs_without_the_value(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(svc, "_slog", recorder)
    raw = {"v": 1, "env": {"GITHUB_TOKEN": "ghp_" + "Sentinel" * 5}}
    assert svc.validated_or_none(raw, run_id="run-sentinel-1") is None
    assert [(level, event) for level, event, _ in recorder.lines] == [("warning", "run_context_dropped")]
    fields = recorder.lines[0][2]
    assert fields["run_id"] == "run-sentinel-1" and fields["error_count"] == 1
    assert "Sentinel" not in repr(recorder.lines)
    assert svc.validated_or_none(None) is None and len(recorder.lines) == 1  # None is not "dropped"


def test_parse_form_run_context_refuses_with_the_first_sentence():
    with pytest.raises(svc.RunContextRefused) as caught:
        svc.parse_form_run_context(json.dumps({"v": 1, "dependency_fingerprints": {"poetry.lock.bak": "0f" * 32}}))
    assert caught.value.detail() == {
        "code": "invalid_run_context", "message": RUN_CONTEXT_ERRORS["lockfile_name"],
    }
    assert svc.parse_form_run_context(None) is None
    assert svc.parse_form_run_context("   ") is None
    # A direct handler call passes FastAPI's truthy Form(None) default object: not sent.
    from fastapi import Form

    assert svc.parse_form_run_context(Form(None, max_length=16384)) is None
    stored = svc.parse_form_run_context(json.dumps({"v": 1, "env": _secret_samples()}))
    assert "sentinel" not in json.dumps(stored).lower()


def test_stamp_run_context_fills_only_a_null_column():
    run = SimpleNamespace(run_context=None)
    first = {"v": 1, "runtime": {"python": "3.11.8"}}
    assert svc.stamp_run_context(run, first) is True and run.run_context == first
    assert svc.stamp_run_context(run, {"v": 1, "runtime": {"python": "3.11.9"}}) is False
    assert run.run_context == first
    empty = SimpleNamespace(run_context=None)
    assert svc.stamp_run_context(empty, None) is False and empty.run_context is None


def test_fold_run_context_drops_a_caller_copy_and_keeps_the_typed_field():
    forged = {"team": "payments", "run_context": {"v": 1, "env": {"HOME": "/forged"}}}
    assert svc.fold_run_context(forged, SimpleNamespace(run_context=None)) == {"team": "payments"}
    typed = RunContext.model_validate({"v": 1, "env": {"TZ": "UTC"}})
    assert svc.fold_run_context(forged, SimpleNamespace(run_context=typed)) == {
        "team": "payments", "run_context": {"v": 1, "env": {"TZ": "UTC"}},
    }
    untouched = {"team": "payments"}
    assert svc.fold_run_context(untouched, SimpleNamespace()) is untouched
    assert svc.fold_run_context(None, None) == {}
