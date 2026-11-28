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
    component: format(index, "x") * 64
    for index, component in enumerate(manifests.COMPONENTS, start=1)
}


def image(component: str) -> str:
    return f"{manifests.EXPECTED_SOURCE_NAMES[component]}@sha256:{DIGESTS[component]}"


def valid_manifest() -> dict:
    return manifests.create_manifest(
        source_sha=SOURCE_SHA,
        version="v1.2.3",
        images={component: image(component) for component in manifests.COMPONENTS},
    )


def test_create_and_validate_requires_all_release_components():
    manifest = valid_manifest()

    assert manifests.validate_manifest(manifest, expected_source_sha=SOURCE_SHA) == manifest
    assert list(manifest["images"]) == list(manifests.COMPONENTS)


def test_legacy_application_only_manifest_remains_readable():
    legacy = valid_manifest()
    legacy["schema_version"] = manifests.LEGACY_SCHEMA_VERSION
    legacy["images"] = {
        component: legacy["images"][component]
        for component in manifests.APPLICATION_COMPONENTS
    }

    assert list(manifests.validate_manifest(legacy)["images"]) == list(manifests.APPLICATION_COMPONENTS)


def test_deployment_schema_gate_rejects_legacy_but_keeps_inspection_compatible():
    legacy = valid_manifest()
    legacy["schema_version"] = manifests.LEGACY_SCHEMA_VERSION
    legacy["images"] = {
        component: legacy["images"][component]
        for component in manifests.APPLICATION_COMPONENTS
    }

    inspected = manifests.validate_manifest(legacy)
    with pytest.raises(manifests.ManifestError, match="inspection only.*schema_version 2"):
        manifests.require_schema_version(inspected, manifests.SCHEMA_VERSION)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda manifest: manifest["images"]["backend"].__setitem__("source_ref", "ghcr.io/test-intelligence/testlookup/backend:v1.2.3"), "immutable"),
        (lambda manifest: manifest["images"].pop("mcp"), "exactly"),
        (lambda manifest: manifest["images"]["postgres"].__setitem__("source_ref", image("mongo")), "must lock postgres"),
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

    expected = {}
    for component in manifests.COMPONENTS:
        if component in manifests.APPLICATION_COMPONENTS:
            path = f"testlookup/{component}"
        else:
            path = manifests.EXPECTED_SOURCE_NAMES[component].rsplit(":", 1)[0]
        expected[component] = f"us-central1-docker.pkg.dev/example/release/{path}@sha256:{DIGESTS[component]}"
    assert materialized["destination_refs"] == expected
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
    command.extend(("--image", "backend=ghcr.io/test-intelligence/testlookup/backend:v1.2.3"))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "immutable" in result.stderr


def test_refs_cli_emits_the_complete_immutable_infrastructure_set(tmp_path: Path):
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps(valid_manifest()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(Path(manifests.__file__)), "refs", "--manifest", str(manifest),
         "--kind", "infrastructure"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        f"{component}\t{image(component)}" for component in manifests.INFRASTRUCTURE_COMPONENTS
    ]


def test_refs_cli_materializes_private_registry_digests(tmp_path: Path):
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps(valid_manifest()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(Path(manifests.__file__)), "refs", "--manifest", str(manifest),
         "--kind", "all", "--destination-prefix", "pull.internal.example/releases"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    refs = dict(line.split("\t", 1) for line in result.stdout.splitlines())
    assert refs["backend"] == (
        f"pull.internal.example/releases/testlookup/backend@sha256:{DIGESTS['backend']}"
    )
    assert refs["versitygw"] == (
        "pull.internal.example/releases/ghcr.io/versity/versitygw"
        f"@sha256:{DIGESTS['versitygw']}"
    )
    assert all("@sha256:" in ref for ref in refs.values())
