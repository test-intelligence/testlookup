"""D-12 / S-C-1 / S-C-2: the token-shape pass keeps digests and file paths, never a real token.

Pure. Secret samples are built at run time from a seeded alphabet, so no secret-shaped literal sits in
this file. Digests are real ``hashlib`` output.
"""
from __future__ import annotations

import base64
import hashlib
import random
import re
import string

import pytest

from app.services.redaction_service import REDACTED
from app.services.run_context_vocab import TOKEN_SHAPES
from app.services.token_shape_pass import GENERIC_SHAPE, is_path_like, token_pass

_RNG = random.Random(20261121)
_ALNUM = string.ascii_letters + string.digits
_B64 = _ALNUM + "+/"


def _pick(count: int, alphabet: str = _ALNUM) -> str:
    return "".join(_RNG.choice(alphabet) for _ in range(count))


def _mixed_b64(count: int) -> str:
    """Random standard base64 holding upper, lower, a digit, ``/`` and ``+`` (an AWS secret key's shape)."""
    while True:
        value = _pick(count, _B64)
        if all(any(ch in group for ch in value) for group in (string.ascii_uppercase, string.ascii_lowercase,
                                                               string.digits, "/", "+")):
            return value


def _jwt() -> str:
    head = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(b'{"sub":"' + _pick(12).encode() + b'","iat":1700000000}').decode().rstrip("=")
    return f"{head}.{body}.{_pick(43, _ALNUM + '-_')}"


TOKENS = {
    "jwt": _jwt(),
    "github": "ghp" + "_" + _pick(36),
    "aws_key": "AKIA" + _pick(16, string.ascii_uppercase + string.digits),
    "aws_secret": _mixed_b64(40),
    "b64_secret": _mixed_b64(40).replace("+", "/"),          # no "+": the path test alone must refuse it
    "b64_long": _mixed_b64(64),
}

SHA1 = hashlib.sha1(b"commit 1").hexdigest()
SHA256 = hashlib.sha256(b"poetry.lock bytes").hexdigest()
SHA512_HEX = hashlib.sha512(b"layer").hexdigest()
SRI_512 = "sha512-" + base64.b64encode(hashlib.sha512(b"left-pad-1.3.0.tgz").digest()).decode()
SRI_256 = "sha256-" + base64.b64encode(hashlib.sha256(b"lodash-4.17.21.tgz").digest()).decode()

KEPT = [
    "sha256:" + SHA256,                                                    # an image digest (DIGEST_RE)
    "registry.example.test/payments/api@sha256:" + SHA256,
    "sha1:" + SHA1,
    "sha512:" + SHA512_HEX,
    SRI_512,
    SRI_256,
    "backend/app/services/commit_attribution_service.py",
    "com/storefront/checkout/coupon/CouponRoundingService.java:118",
    "src/pages/checkout/components/CouponSummaryPanel.tsx",
    "org/springframework/boot/autoconfigure/web/servlet/DispatcherServletAutoConfiguration",
    "frontend/node_modules/some-really-long-package-name/dist/XMLHttpRequestHandler.js",
    "3f1c9e2a-6b7d-4e8f-9a0b-1c2d3e4f5a6b/builds/build-2001/7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d/",
    "/home/runner/work/payments/payments/tests/integration/test_refund_rejected_when_expired",
    "META-INF/maven/com.example/payments-service-integration-tests/pom.properties",
]


@pytest.mark.parametrize("value", KEPT)
def test_digests_and_paths_survive_byte_exact(value: str) -> None:
    assert token_pass(value) == value
    sentence = f"at {value} (failed)"
    assert token_pass(sentence) == sentence


def test_the_samples_would_have_been_redacted_by_the_whole_generic_rule() -> None:
    """Guard: each kept sample really carries a 40+ run (else the test above proves nothing)."""
    generic = re.compile(GENERIC_SHAPE)
    for value in KEPT:
        assert generic.search(value), value


@pytest.mark.parametrize("name", sorted(TOKENS))
def test_real_tokens_are_still_redacted(name: str) -> None:
    token = TOKENS[name]
    out = token_pass(f"export X={token} then retry")
    assert token not in out and REDACTED in out, name
    # No fragment of 8+ characters is left behind. A JWT's HEADER is a public constant the token rule leaves;
    # the whole-JWT rule is ``redact_text`` / ``project_export_scrub._JWT`` (asserted in their own suites).
    for part in re.split(r"[./]", token)[1 if name == "jwt" else 0:]:
        if len(part) >= 8:
            assert part not in out, (name, part)


def test_a_token_inside_a_path_segment_is_redacted() -> None:
    long_segment = _pick(44, string.ascii_lowercase + string.digits)
    out = token_pass(f"src/cache/{long_segment}/index.js")
    assert out == f"src/cache/{REDACTED}/index.js"
    assert token_pass("payments/" + TOKENS["github"] + "/run") == f"payments/{REDACTED}/run"


def test_a_bare_hex_run_is_still_redacted() -> None:
    """Deliberately not exempt: ``openssl rand -hex 32`` secrets and 40-hex OAuth tokens have this shape.
    Fields known to hold a digest are exempt by key (``project_export_scrub.JSON_HEX_KEYS``)."""
    assert token_pass(SHA1) == REDACTED
    assert token_pass("secret " + SHA256) == "secret " + REDACTED
    assert token_pass("sha256:" + SHA256.upper()) == "sha256:" + REDACTED         # DIGEST_RE is lower-case
    assert token_pass("sha256:" + SHA1) == "sha256:" + REDACTED                   # wrong length for sha256
    assert token_pass("xsha256:" + SHA256) == "xsha256:" + REDACTED              # not a prefix


def test_random_base64_with_a_slash_is_never_read_as_a_path() -> None:
    kept = []
    for _ in range(5000):
        for size in (40, 44, 60):
            value = _pick(size, _B64)
            if "/" in value and token_pass(value) != REDACTED:
                kept.append(value)
    assert kept == []


def test_path_rule_reads_segments() -> None:
    assert is_path_like("a/b")
    assert not is_path_like("onesegment")
    assert not is_path_like("src/Abc1Def/x")            # upper case mixed with digits is not a name
    assert not is_path_like("src/FsCfxKccIQrbMUZzHv/x")  # two acronym runs
    assert not is_path_like("src/a+b/c")


def test_the_provider_shapes_still_apply_to_the_whole_text() -> None:
    specific = [re.compile(shape) for shape in TOKEN_SHAPES if shape != GENERIC_SHAPE]
    assert len(specific) == len(TOKEN_SHAPES) - 1
    samples = ["ghp_" + _pick(24), "github_pat_" + _pick(24), "glpat-" + _pick(20), "xoxb-" + _pick(12),
               "sk-" + _pick(24), "AKIA" + _pick(16, string.ascii_uppercase)]
    for sample in samples:
        assert any(shape.fullmatch(sample) for shape in specific), sample
        assert token_pass("path/" + sample) == "path/" + REDACTED, sample


@pytest.mark.parametrize("value", KEPT + [f"use {token}" for token in TOKENS.values()])
def test_token_pass_is_idempotent(value: str) -> None:
    once = token_pass(value)
    assert token_pass(once) == once
    assert "[REDACTE" not in once.replace(REDACTED, "")      # a marker is never cut
