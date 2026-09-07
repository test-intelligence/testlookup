"""Regression tests for the immutable release-image manifest contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_manifest as manifests  # noqa: E402


SOURCE_SHA = "a" * 40
DIGESTS = {
    "backend": "1" * 64,
    "frontend": "2" * 64,
    "mcp": "3" * 64,
}


def image(component: str) -> str:
    return f"ghcr.io/acme/testlookup/{component}@sha256:{DIGESTS[component]}"


def valid_manifest() -> dict:
    return manifests.create_manifest(
        source_sha=SOURCE_SHA,
        version="v1.2.3",
        images={component: image(component) for component in manifests.COMPONENTS},
    )


def test_create_and_validate_requires_all_release_components():
    manifest = valid_manifest()

    assert manifests.validate_manifest(manifest, expected_source_sha=SOURCE_SHA) == manifest
    assert list(manifest["images"]) == ["backend", "frontend", "mcp"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest["images"]["backend"].__setitem__("source_ref", "ghcr.io/acme/testlookup/backend:v1.2.3"), "immutable"),
        (lambda manifest: manifest["images"].pop("mcp"), "exactly"),
        (lambda manifest: manifest.__setitem__("source_sha", "A" * 40), "lowercase"),
        (lambda manifest: manifest.__setitem__("surprise", "unreviewed"), "unexpected"),
    ],
)
def test_validate_rejects_mutable_or_incomplete_release_identity(mutate, message):
    manifest = valid_manifest()
    mutate(manifest)

    with pytest.raises(manifests.ManifestError, match=message):
        manifests.validate_manifest(manifest)


def test_materialization_preserves_each_verified_digest_without_a_tag():
    materialized = manifests.materialize_destination_refs(
        valid_manifest(), destination_prefix="us-central1-docker.pkg.dev/example/release"
    )

    assert materialized["destination_refs"] == {
        component: f"us-central1-docker.pkg.dev/example/release/testlookup/{component}@sha256:{DIGESTS[component]}"
        for component in manifests.COMPONENTS
    }
    assert all(":v" not in ref for ref in materialized["destination_refs"].values())
    assert manifests.validate_materialized_manifest(
        materialized,
        expected_source_sha=SOURCE_SHA,
        expected_destination_prefix="us-central1-docker.pkg.dev/example/release",
    ) == materialized


def test_materialized_manifest_rejects_a_destination_digest_swap():
    materialized = manifests.materialize_destination_refs(valid_manifest(), destination_prefix="registry.example/release")
    materialized["destination_refs"]["frontend"] = materialized["destination_refs"]["backend"]

    with pytest.raises(manifests.ManifestError, match="does not preserve"):
        manifests.validate_materialized_manifest(materialized)


def test_cli_round_trip_writes_then_validates_then_materializes(tmp_path: Path):
    script = Path(manifests.__file__)
    base = tmp_path / "release.json"
    deployment = tmp_path / "deployment.json"
    create = [
        sys.executable,
        str(script),
        "create",
        "--source-sha",
        SOURCE_SHA,
        "--version",
        "v1.2.3",
        "--output",
        str(base),
    ]
    for component in manifests.COMPONENTS:
        create.extend(("--image", f"{component}={image(component)}"))
    assert subprocess.run(create, check=False, capture_output=True, text=True).returncode == 0
    assert subprocess.run(
        [sys.executable, str(script), "validate", "--manifest", str(base), "--expected-source-sha", SOURCE_SHA],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    assert subprocess.run(
        [
            sys.executable,
            str(script),
            "materialize",
            "--manifest",
            str(base),
            "--destination-prefix",
            "registry.example/team",
            "--output",
            str(deployment),
        ],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    written = json.loads(deployment.read_text(encoding="utf-8"))
    assert written["destination_refs"]["mcp"].endswith(f"@sha256:{DIGESTS['mcp']}")


def test_cli_returns_nonzero_when_a_mutable_tag_is_supplied(tmp_path: Path):
    script = Path(manifests.__file__)
    command = [
        sys.executable,
        str(script),
        "create",
        "--source-sha",
        SOURCE_SHA,
        "--version",
        "v1.2.3",
        "--output",
        str(tmp_path / "release.json"),
    ]
    command.extend(("--image", "backend=ghcr.io/acme/testlookup/backend:v1.2.3"))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "immutable" in result.stderr
