#!/usr/bin/env python3
"""Create and verify the immutable image set for a TestLookup release.

The release workflow builds the three application images once, records their
registry digests here, and later deployment workflows materialize that same
set under a destination registry.  A tag is deliberately not accepted as an
image identity: tags may be moved after verification while a digest cannot.

The module intentionally uses only the Python standard library so it can run
in a GitHub Actions verification job before application dependencies exist.

Examples::

    python scripts/release/release_manifest.py create \
      --source-sha "$GITHUB_SHA" --version v1.2.3 \
      --image backend=ghcr.io/acme/testlookup/backend@sha256:... \
      --image frontend=ghcr.io/acme/testlookup/frontend@sha256:... \
      --image mcp=ghcr.io/acme/testlookup/mcp@sha256:... \
      --output release-manifest.json

    python scripts/release/release_manifest.py materialize \
      --manifest release-manifest.json \
      --destination-prefix us-central1-docker.pkg.dev/project/releases \
      --output deployment-manifest.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
COMPONENTS = ("backend", "frontend", "mcp")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMMUTABLE_REF_RE = re.compile(r"^(?P<name>[^\s@]+)@(?P<digest>sha256:[0-9a-f]{64})$")
_DESTINATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class ManifestError(ValueError):
    """A release manifest fails its untrusted-input contract."""


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    if value != value.strip() or any(ch.isspace() for ch in value):
        raise ManifestError(f"{field} must not contain whitespace")
    return value


def _validate_sha(value: object, field: str = "source_sha") -> str:
    sha = _require_string(value, field)
    if not _SHA_RE.fullmatch(sha):
        raise ManifestError(f"{field} must be a lowercase 40 or 64 character Git SHA")
    return sha


def _validate_version(value: object) -> str:
    version = _require_string(value, "version")
    if not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ManifestError("version must be a semantic version such as v1.2.3")
    return version


def _validate_immutable_ref(value: object, field: str) -> str:
    ref = _require_string(value, field)
    match = _IMMUTABLE_REF_RE.fullmatch(ref)
    if not match:
        raise ManifestError(f"{field} must be an immutable image reference ending in @sha256:<64 hex>")
    name = match.group("name")
    if name.startswith(("/", ".")) or "//" in name or "://" in name:
        raise ManifestError(f"{field} has an invalid image name")
    return ref


def _split_immutable_ref(ref: str) -> tuple[str, str]:
    match = _IMMUTABLE_REF_RE.fullmatch(ref)
    if match is None:  # Defensive: callers invoke this only after validation.
        raise ManifestError("image reference is not immutable")
    return match.group("name"), match.group("digest")


def _validate_images(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        raise ManifestError("images must be an object keyed by component")
    names = set(value)
    expected = set(COMPONENTS)
    if names != expected:
        missing = ", ".join(sorted(expected - names))
        unexpected = ", ".join(sorted(names - expected))
        details = ", ".join(part for part in (
            f"missing: {missing}" if missing else "",
            f"unexpected: {unexpected}" if unexpected else "",
        ) if part)
        raise ManifestError(f"images must contain exactly {', '.join(COMPONENTS)} ({details})")

    images: dict[str, dict[str, str]] = {}
    seen_digests: set[str] = set()
    for component in COMPONENTS:
        entry = value[component]
        if not isinstance(entry, Mapping) or set(entry) != {"source_ref"}:
            raise ManifestError(f"images.{component} must contain only source_ref")
        source_ref = _validate_immutable_ref(entry["source_ref"], f"images.{component}.source_ref")
        _, digest = _split_immutable_ref(source_ref)
        if digest in seen_digests:
            raise ManifestError("each component must use a distinct immutable image digest")
        seen_digests.add(digest)
        images[component] = {"source_ref": source_ref}
    return images


def validate_manifest(manifest: object, *, expected_source_sha: str | None = None) -> dict[str, Any]:
    """Validate and normalize a base release manifest.

    The return value contains only fields safe for a deployment workflow to
    consume.  Rejecting unknown fields makes a review-visible manifest the
    complete release identity, rather than an object where a consumer can
    silently interpret an unreviewed extension.
    """

    if not isinstance(manifest, Mapping):
        raise ManifestError("manifest must be a JSON object")
    required = {"schema_version", "source_sha", "version", "images"}
    actual = set(manifest)
    if actual != required:
        missing = ", ".join(sorted(required - actual))
        unexpected = ", ".join(sorted(actual - required))
        details = ", ".join(part for part in (
            f"missing: {missing}" if missing else "",
            f"unexpected: {unexpected}" if unexpected else "",
        ) if part)
        raise ManifestError(f"manifest fields must be exactly {', '.join(sorted(required))} ({details})")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(f"schema_version must be {SCHEMA_VERSION}")

    source_sha = _validate_sha(manifest["source_sha"])
    if expected_source_sha is not None and source_sha != _validate_sha(expected_source_sha, "expected_source_sha"):
        raise ManifestError("manifest source_sha does not match the verified source SHA")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_sha": source_sha,
        "version": _validate_version(manifest["version"]),
        "images": _validate_images(manifest["images"]),
    }


def create_manifest(*, source_sha: str, version: str, images: Mapping[str, str]) -> dict[str, Any]:
    """Create a normalized immutable release manifest from component refs."""

    # Validate values before the complete-set check below.  A release command
    # with one mutable tag should identify that security boundary directly,
    # rather than reporting only that the remaining components were omitted.
    for component, ref in images.items():
        _validate_immutable_ref(ref, f"images.{component}.source_ref")
    return validate_manifest({
        "schema_version": SCHEMA_VERSION,
        "source_sha": source_sha,
        "version": version,
        "images": {name: {"source_ref": ref} for name, ref in images.items()},
    })


def _validate_destination_prefix(value: object) -> str:
    prefix = _require_string(value, "destination_prefix").rstrip("/")
    if not prefix or not _DESTINATION_RE.fullmatch(prefix):
        raise ManifestError("destination_prefix must be a registry/path without a scheme or whitespace")
    if prefix.startswith((".", "/", "-")) or "//" in prefix or "://" in prefix or "@" in prefix:
        raise ManifestError("destination_prefix must be a registry/path without a scheme or digest")
    return prefix


def materialize_destination_refs(manifest: object, *, destination_prefix: str) -> dict[str, Any]:
    """Map one verified image set to immutable refs under a destination registry.

    Copying images is deliberately outside this function.  A release workflow
    can first use its registry-copy tool to copy each ``source_ref`` to the
    returned ``destination_ref`` and only then deploy this returned document.
    This prevents an environment from rebuilding or resolving a mutable tag.
    """

    normalized = validate_manifest(manifest)
    prefix = _validate_destination_prefix(destination_prefix)
    destination_refs: dict[str, str] = {}
    for component in COMPONENTS:
        _, digest = _split_immutable_ref(normalized["images"][component]["source_ref"])
        destination_refs[component] = f"{prefix}/testlookup/{component}@{digest}"
    return {**normalized, "destination_prefix": prefix, "destination_refs": destination_refs}


def validate_materialized_manifest(
    materialized: object,
    *,
    expected_source_sha: str | None = None,
    expected_destination_prefix: str | None = None,
) -> dict[str, Any]:
    """Validate a deployment-ready materialization and its digest identity."""

    if not isinstance(materialized, Mapping):
        raise ManifestError("materialized manifest must be a JSON object")
    base_fields = {"schema_version", "source_sha", "version", "images"}
    required = base_fields | {"destination_prefix", "destination_refs"}
    if set(materialized) != required:
        raise ManifestError("materialized manifest has missing or unexpected fields")
    base = validate_manifest({key: materialized[key] for key in base_fields}, expected_source_sha=expected_source_sha)
    prefix = _validate_destination_prefix(materialized["destination_prefix"])
    if expected_destination_prefix is not None and prefix != _validate_destination_prefix(expected_destination_prefix):
        raise ManifestError("materialized destination_prefix does not match the requested destination")
    refs = materialized["destination_refs"]
    if not isinstance(refs, Mapping) or set(refs) != set(COMPONENTS):
        raise ManifestError("destination_refs must contain exactly backend, frontend, mcp")
    for component in COMPONENTS:
        _, digest = _split_immutable_ref(base["images"][component]["source_ref"])
        expected = f"{prefix}/testlookup/{component}@{digest}"
        if refs[component] != expected:
            raise ManifestError(f"destination_refs.{component} does not preserve the verified source digest")
    return {**base, "destination_prefix": prefix, "destination_refs": dict(refs)}


def load_manifest(path: Path) -> dict[str, Any]:
    """Load JSON with a useful path-qualified error."""

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    return validate_manifest(loaded)


def _write_json(document: Mapping[str, Any], output: str) -> None:
    serialized = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if output == "-":
        sys.stdout.write(serialized)
        return
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _image_args(values: list[str]) -> dict[str, str]:
    images: dict[str, str] = {}
    for value in values:
        component, separator, ref = value.partition("=")
        if not separator or not component or not ref:
            raise ManifestError("--image must be COMPONENT=IMAGE@sha256:<64 hex>")
        if component in images:
            raise ManifestError(f"component {component!r} was supplied more than once")
        images[component] = ref
    return images


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="write an immutable release manifest")
    create.add_argument("--source-sha", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--image", action="append", default=[], help="COMPONENT=IMAGE@sha256:<digest>")
    create.add_argument("--output", required=True, help="path to write, or - for stdout")

    validate = commands.add_parser("validate", help="validate an immutable release manifest")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--expected-source-sha")

    materialize = commands.add_parser("materialize", help="produce destination digest refs from a release manifest")
    materialize.add_argument("--manifest", required=True)
    materialize.add_argument("--destination-prefix", required=True)
    materialize.add_argument("--output", required=True, help="path to write, or - for stdout")

    validate_destination = commands.add_parser("validate-materialized", help="validate a deployment-ready manifest")
    validate_destination.add_argument("--manifest", required=True)
    validate_destination.add_argument("--expected-source-sha")
    validate_destination.add_argument("--expected-destination-prefix")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            _write_json(
                create_manifest(source_sha=args.source_sha, version=args.version, images=_image_args(args.image)),
                args.output,
            )
        elif args.command == "validate":
            validate_manifest(
                json.loads(Path(args.manifest).read_text(encoding="utf-8")),
                expected_source_sha=args.expected_source_sha,
            )
            print(f"valid release manifest: {args.manifest}")
        elif args.command == "materialize":
            manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            _write_json(materialize_destination_refs(manifest, destination_prefix=args.destination_prefix), args.output)
        elif args.command == "validate-materialized":
            validate_materialized_manifest(
                json.loads(Path(args.manifest).read_text(encoding="utf-8")),
                expected_source_sha=args.expected_source_sha,
                expected_destination_prefix=args.expected_destination_prefix,
            )
            print(f"valid materialized release manifest: {args.manifest}")
        else:  # argparse makes this unreachable; keep the return contract total.
            raise ManifestError(f"unknown command: {args.command}")
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"release manifest error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
