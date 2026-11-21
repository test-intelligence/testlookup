"""The K3s deploy never lets its registry decide which image is "the build".

Security review 2026-11-20, TL-02. The bundled registry speaks plain HTTP with
no authentication on a NodePort. ``k3ssetup/deploy-k3s.sh`` pushed three
tagged images and then asked that registry which digest each tag has; with
``--skip-build`` it asked the registry for both the tag and the digest. The
answer became the image of the migration Job and of every workload. Whoever
can write to the registry, or sit between it and the deploy host, chose the
image that receives the database and every secret.

The rule: the digest comes from the side that built the image.

* a build takes it from the container engine that pushed (``push_image``),
  and the registry's answer must be one the engine reported
  (``confirm_pushed_digest``);
* ``--skip-build`` takes it from the record that build wrote, or from digests
  the operator supplies, and the registry must serve exactly that
  (``reused_image_digest``);
* trusting the registry's answer is an explicit switch
  (``K3S_TRUST_REGISTRY_DIGESTS=true``), never the fallback.

The functions are run for real, with ``curl`` and the engine replaced by shell
functions.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.regression.test_k3s_build_authority import BASH, _extract, _run_script

pytestmark = [pytest.mark.regression, pytest.mark.skipif(BASH is None, reason="requires POSIX bash")]

BUILT = "sha256:" + "a" * 64
OLDER = "sha256:" + "c" * 64
PLANTED = "sha256:" + "b" * 64


def _functions(*names: str) -> str:
    return "\n".join(textwrap.dedent(_extract(name)) for name in names)


def _registry(digest: str) -> str:
    """A ``curl`` that answers a manifest HEAD the way the registry does, with ``digest``."""
    return (
        "curl() {\n"
        "  printf 'HTTP/1.1 200 OK\\r\\n'\n"
        "  printf 'Content-Type: application/vnd.oci.image.manifest.v1+json\\r\\n'\n"
        f"  printf 'Docker-Content-Digest: {digest}\\r\\n'\n"
        "}\n"
    )


# ── a build ──────────────────────────────────────────────────────────────────


def _engine(kind: str, digests: list[str]) -> str:
    """A container engine that 'pushes' and reports ``digests`` the way ``kind`` does."""
    if kind == "podman":
        return (
            "CONTAINER_ENGINE=engine\n"
            "engine() {\n"
            "  case \"$1\" in\n"
            "    --version) echo 'podman version 6.0.2' ;;\n"
            "    push) [ \"$2\" = --digestfile ] || { echo 'no --digestfile' >&2; return 1; }\n"
            f"          printf '%s' '{digests[0]}' > \"$3\"; echo \"pushed $4\" ;;\n"
            "  esac\n"
            "}\n"
        )
    rows = "".join(f"reg.example.test:30500/testlookup/backend@{d}\\n" for d in digests)
    return (
        "CONTAINER_ENGINE=engine\n"
        "engine() {\n"
        "  case \"$1\" in\n"
        "    --version) echo 'Docker version 27.0.0, build abc' ;;\n"
        "    push) echo \"pushed $2\" ;;\n"
        f"    image) printf '{rows}other.example.test/testlookup/backend@{PLANTED}\\n' ;;\n"
        "  esac\n"
        "}\n"
    )


def _build(tmp_path: Path, kind: str, engine_digests: list[str], registry_digest: str):
    body = (
        _engine(kind, engine_digests) + _registry(registry_digest)
        + _functions("registry_manifest_digest", "push_image", "confirm_pushed_digest")
        + "\npushed=$(push_image reg.example.test:30500/testlookup/backend:build-1) || { echo NO-ENGINE-DIGEST; exit 3; }\n"
        "digest=$(confirm_pushed_digest reg.example.test:30500 backend build-1 \"$pushed\") || { echo REFUSED; exit 4; }\n"
        "echo \"DEPLOY=$digest\"\n"
    )
    return _run_script(tmp_path, body)


@pytest.mark.parametrize("kind", ["podman", "docker"])
def test_a_build_deploys_the_digest_its_engine_pushed(tmp_path: Path, kind: str):
    result = _build(tmp_path, kind, [BUILT], BUILT)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == f"DEPLOY={BUILT}"


@pytest.mark.parametrize("kind", ["podman", "docker"])
def test_a_registry_that_answers_with_another_image_stops_the_build_deploy(tmp_path: Path, kind: str):
    """The tag was overwritten, or the answer was changed on the way: not the image that was built."""
    result = _build(tmp_path, kind, [BUILT], PLANTED)

    assert result.returncode == 4, result.stdout + result.stderr
    assert "REFUSED" in result.stdout and "DEPLOY=" not in result.stdout


def test_docker_may_list_an_older_push_but_only_its_own_repository_counts(tmp_path: Path):
    # Two pushes of the same image to this repository: either is the engine's own.
    assert _build(tmp_path, "docker", [OLDER, BUILT], BUILT).returncode == 0
    # A digest the engine holds for ANOTHER repository does not vouch for this one.
    result = _build(tmp_path, "docker", [BUILT], PLANTED)
    assert result.returncode == 4 and "DEPLOY=" not in result.stdout


def test_an_engine_that_reports_no_digest_stops_the_deploy(tmp_path: Path):
    result = _build(tmp_path, "podman", ["not-a-digest"], BUILT)

    assert result.returncode == 3
    assert "NO-ENGINE-DIGEST" in result.stdout


# ── --skip-build ─────────────────────────────────────────────────────────────


def _reuse(tmp_path: Path, *, registry_digest: str, record: str | None = None, operator: str = "",
           trust_registry: bool = False):
    records = tmp_path / "records"
    records.mkdir(exist_ok=True)
    if record is not None:
        (records / "build-1.digests").write_text(
            f"backend={record}\nfrontend={record}\nmcp={record}\nrevision=abc\n", encoding="utf-8", newline="\n",
        )
    body = (
        _registry(registry_digest)
        + ("K3S_TRUST_REGISTRY_DIGESTS=true\n" if trust_registry else "")
        + _functions("registry_manifest_digest", "reused_image_digest")
        + f"\ndigest=$(reused_image_digest records build-1 backend '{operator}' reg.example.test:30500)"
        " || { echo REFUSED; exit 4; }\n"
        "echo \"DEPLOY=$digest\"\n"
    )
    return _run_script(tmp_path, body)


def test_a_reuse_deploys_the_digest_the_build_recorded(tmp_path: Path):
    result = _reuse(tmp_path, registry_digest=BUILT, record=BUILT)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"DEPLOY={BUILT}"


def test_a_reuse_stops_when_the_registry_no_longer_serves_the_recorded_image(tmp_path: Path):
    result = _reuse(tmp_path, registry_digest=PLANTED, record=BUILT)

    assert result.returncode == 4
    assert "DEPLOY=" not in result.stdout
    assert PLANTED in result.stderr and BUILT in result.stderr
    assert "Nothing was deployed" in result.stderr


def test_a_reuse_without_a_record_does_not_take_the_registrys_word(tmp_path: Path):
    """The case the review describes: nothing local says what the tag should be."""
    result = _reuse(tmp_path, registry_digest=PLANTED)

    assert result.returncode == 4
    assert "DEPLOY=" not in result.stdout
    assert "no build record" in result.stderr
    assert "K3S_TRUST_REGISTRY_DIGESTS" in result.stderr


def test_supplied_digests_win_over_the_record_and_bind_the_registry(tmp_path: Path):
    assert _reuse(tmp_path, registry_digest=BUILT, operator=BUILT).stdout.strip() == f"DEPLOY={BUILT}"
    assert _reuse(tmp_path, registry_digest=BUILT, record=OLDER, operator=BUILT).returncode == 0

    refused = _reuse(tmp_path, registry_digest=PLANTED, operator=BUILT)
    assert refused.returncode == 4 and "K3S_BACKEND_DIGEST" in refused.stderr

    malformed = _reuse(tmp_path, registry_digest=BUILT, operator="latest")
    assert malformed.returncode == 4 and "not a sha256 digest" in malformed.stderr


def test_trusting_the_registry_is_an_explicit_switch_and_says_so(tmp_path: Path):
    result = _reuse(tmp_path, registry_digest=PLANTED, trust_registry=True)

    assert result.returncode == 0
    assert result.stdout.strip() == f"DEPLOY={PLANTED}"
    assert "WARNING" in result.stderr and "K3S_TRUST_REGISTRY_DIGESTS=true" in result.stderr


def test_a_build_writes_the_record_a_reuse_reads(tmp_path: Path):
    body = (
        _registry(BUILT) + _functions("registry_manifest_digest", "write_build_record", "reused_image_digest")
        + f"\nwrite_build_record records build-1 {BUILT} {OLDER} {PLANTED} deadbeef\n"
        "cat records/build-1.digests\n"
        "reused_image_digest records build-1 backend '' reg.example.test:30500\n"
    )
    result = _run_script(tmp_path, body)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        f"backend={BUILT}", f"frontend={OLDER}", f"mcp={PLANTED}", "revision=deadbeef", BUILT,
    ]
    assert not list((tmp_path / "records").glob(".*.tmp"))
