#!/usr/bin/env python3
"""Guard: every deployment surface must agree with deploy/images.manifest.txt.

Container images are named in four places that historically drifted apart:

  * ``docker-compose{,.release,.dev-lite,.airgap}.yml``   -- Compose
  * ``k8s/base/**`` + ``k8s/overlays/**``                 -- Kubernetes
  * ``k8s/overlays/openshift-artifactory/kustomization.yaml`` ``images:`` block
  * ``openshiftsetup/*.sh``                               -- the mirror list

The offline bundle (``make offline-bundle``) is built from the manifest, so a
surface that disagrees with the manifest ships a bundle that is missing an
image the deployment actually needs -- an ImagePullBackOff on a customer's
air-gapped cluster, discovered at the worst possible time.

This script fails CI when any of them diverge. It is stdlib-only on purpose:
the CI job that runs it installs nothing (no PyYAML), matching the existing
``quality-gate`` job's constraint.

Usage:
    python scripts/release/check_image_drift.py [--repo-root PATH]

Exit codes: 0 = all surfaces agree, 1 = drift found.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Compose files whose literal image: lines are covered by the manifest.
# docker-compose.monitoring.yml (prometheus/grafana/jaeger) and
# docker-compose.gcp-vm.yml (a build-only override with no image: lines) are
# deliberately out of scope: the monitoring sidecar stack is not part of the
# TestLookup application bundle. They are still scanned for :latest below.
MANIFEST_COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.release.yml",
    "docker-compose.dev-lite.yml",
)

AIRGAP_COMPOSE_FILE = "docker-compose.airgap.yml"
OPENSHIFT_KUSTOMIZATION = "k8s/overlays/openshift-artifactory/kustomization.yaml"
OPENSHIFT_SCRIPTS = (
    "openshiftsetup/mirror-images.sh",
    "openshiftsetup/deploy-openshift-artifactory.sh",
)

# The ref is captured greedily rather than as \S+: an air-gap override writes
# `image: ${VAR:?message with spaces}/postgres:16-alpine`, which is one YAML
# scalar but several whitespace-separated words.
IMAGE_LINE = re.compile(r"^\s*image:\s*(?P<ref>\S.*?)\s*$")
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
APP_TAG_TOKEN = "@APP_TAG"


class Row:
    """One line of deploy/images.manifest.txt."""

    __slots__ = ("ref", "role", "surfaces", "bundle", "lineno")

    def __init__(self, ref: str, role: str, surfaces: str, bundle: str, lineno: int):
        self.ref = ref
        self.role = role
        self.surfaces = tuple(s for s in surfaces.split(",") if s)
        self.bundle = bundle
        self.lineno = lineno

    @property
    def name(self) -> str:
        return self.ref.rsplit(":", 1)[0]

    @property
    def tag(self) -> str:
        return self.ref.rsplit(":", 1)[1] if ":" in self.ref else ""


def load_manifest(path: Path) -> list[Row]:
    rows: list[Row] = []
    valid_roles = {"app", "infra"}
    valid_bundles = {"core", "llm", "excluded"}
    valid_surfaces = {"compose", "k8s"}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise SystemExit(
                f"{path}:{lineno}: expected 4 columns (ref role surfaces bundle), got {len(parts)}: {raw!r}"
            )
        row = Row(*parts, lineno=lineno)
        if row.role not in valid_roles:
            raise SystemExit(f"{path}:{lineno}: unknown role {row.role!r}")
        if row.bundle not in valid_bundles:
            raise SystemExit(f"{path}:{lineno}: unknown bundle class {row.bundle!r}")
        for surface in row.surfaces:
            if surface not in valid_surfaces:
                raise SystemExit(f"{path}:{lineno}: unknown surface {surface!r}")
        if row.role == "app" and not row.ref.endswith(APP_TAG_TOKEN):
            raise SystemExit(
                f"{path}:{lineno}: app images must carry the literal tag {APP_TAG_TOKEN} "
                "(the real tag is resolved at build time)"
            )
        rows.append(row)
    if not rows:
        raise SystemExit(f"{path}: manifest is empty")
    return rows


def compose_image_refs(path: Path) -> list[tuple[int, str]]:
    """Literal ``image:`` refs in a compose file (interpolated ones dropped)."""
    out = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = IMAGE_LINE.match(raw)
        if not match:
            continue
        ref = match.group("ref").split(" #", 1)[0].strip().strip('"').strip("'")
        out.append((lineno, ref))
    return out


def strip_registry_prefix(ref: str) -> str:
    """``${VAR:?msg}/minio/minio:TAG`` -> ``minio/minio:TAG``."""
    stripped = INTERPOLATION.sub("", ref, count=1)
    return stripped.lstrip("/")


def yaml_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.yaml") if p.is_file())


def check(repo_root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = repo_root / "deploy" / "images.manifest.txt"
    if not manifest_path.exists():
        return [f"missing manifest: {manifest_path}"]
    rows = load_manifest(manifest_path)

    infra_compose = {r.ref for r in rows if r.role == "infra" and "compose" in r.surfaces}
    infra_k8s = {r.ref for r in rows if r.role == "infra" and "k8s" in r.surfaces}
    app_names = {r.name for r in rows if r.role == "app"}
    all_infra_names = {r.name for r in rows if r.role == "infra"}

    # ── 1. Compose <-> manifest, both directions ────────────────────────────
    seen_compose: set[str] = set()
    for rel in MANIFEST_COMPOSE_FILES:
        path = repo_root / rel
        if not path.exists():
            errors.append(f"{rel}: expected compose file is missing")
            continue
        for lineno, ref in compose_image_refs(path):
            if "${" in ref:
                # App images are parameterized (TESTLOOKUP_IMAGE/VERSION); their
                # tag is a deploy-time choice, so only names are checked.
                bare = strip_registry_prefix(ref).split(":")[0]
                if bare and bare not in app_names and f"testlookup/{bare}" not in app_names:
                    errors.append(
                        f"{rel}:{lineno}: interpolated image {ref!r} does not resolve to a "
                        f"manifest app image ({sorted(app_names)})"
                    )
                continue
            seen_compose.add(ref)
            if ref not in infra_compose:
                name = ref.rsplit(":", 1)[0]
                hint = ""
                if name in all_infra_names:
                    pinned = sorted(r.ref for r in rows if r.name == name)
                    hint = f" — the manifest pins {pinned} for this image"
                errors.append(
                    f"{rel}:{lineno}: image {ref!r} is not in deploy/images.manifest.txt "
                    f"with surface 'compose'{hint}"
                )
    for ref in sorted(infra_compose - seen_compose):
        errors.append(
            f"deploy/images.manifest.txt: {ref!r} is marked surface 'compose' but no compose "
            f"file in {list(MANIFEST_COMPOSE_FILES)} references it"
        )

    # ── 2. Air-gap override must cover exactly the release-compose infra ────
    release = repo_root / "docker-compose.release.yml"
    airgap = repo_root / AIRGAP_COMPOSE_FILE
    if not airgap.exists():
        errors.append(f"{AIRGAP_COMPOSE_FILE}: missing — Compose has no air-gap registry override")
    elif release.exists():
        release_infra = {ref for _, ref in compose_image_refs(release) if "${" not in ref}
        airgap_infra = set()
        for lineno, ref in compose_image_refs(airgap):
            bare = strip_registry_prefix(ref)
            if "${" in bare:  # app image: <reg>/testlookup/x:${TESTLOOKUP_VERSION}
                name = bare.split(":")[0]
                if name not in app_names:
                    errors.append(
                        f"{AIRGAP_COMPOSE_FILE}:{lineno}: {name!r} is not a manifest app image"
                    )
                continue
            airgap_infra.add(bare)
            if bare not in infra_compose:
                errors.append(
                    f"{AIRGAP_COMPOSE_FILE}:{lineno}: image {bare!r} is not in the manifest "
                    "with surface 'compose'"
                )
        for ref in sorted(release_infra - airgap_infra):
            errors.append(
                f"{AIRGAP_COMPOSE_FILE}: docker-compose.release.yml uses {ref!r} but the air-gap "
                "override does not re-point it — that image would be pulled from docker.io"
            )
        for ref in sorted(airgap_infra - release_infra):
            errors.append(
                f"{AIRGAP_COMPOSE_FILE}: re-points {ref!r}, which docker-compose.release.yml "
                "does not use"
            )

    # ── 3. Kubernetes manifests <-> manifest ────────────────────────────────
    seen_k8s: set[str] = set()
    k8s_root = repo_root / "k8s"
    for path in yaml_files(k8s_root):
        rel = path.relative_to(repo_root).as_posix()
        for lineno, ref in compose_image_refs(path):
            name = ref.rsplit(":", 1)[0]
            if name in app_names:
                continue  # app tags are set per-overlay (BUILD_TAG_PLACEHOLDER etc.)
            seen_k8s.add(ref)
            if ref not in infra_k8s:
                hint = ""
                if name in all_infra_names:
                    pinned = sorted(r.ref for r in rows if r.name == name and "k8s" in r.surfaces)
                    hint = f" — the manifest pins {pinned} for k8s"
                errors.append(
                    f"{rel}:{lineno}: image {ref!r} is not in deploy/images.manifest.txt "
                    f"with surface 'k8s'{hint}"
                )
    for ref in sorted(infra_k8s - seen_k8s):
        errors.append(
            f"deploy/images.manifest.txt: {ref!r} is marked surface 'k8s' but no manifest "
            "under k8s/ references it"
        )

    # ── 4. openshift-artifactory kustomization images: block ────────────────
    # This overlay is the air-gap-ready one: it MUST re-point every image, or
    # the cluster silently reaches for docker.io.
    kust = repo_root / OPENSHIFT_KUSTOMIZATION
    if not kust.exists():
        errors.append(f"{OPENSHIFT_KUSTOMIZATION}: missing")
    else:
        lines = kust.read_text(encoding="utf-8").splitlines()
        entries: list[tuple[int, str, str]] = []  # (lineno, name, tag)
        current_name = None
        current_line = 0
        for lineno, raw in enumerate(lines, 1):
            name_m = re.match(r"^\s*-\s*name:\s*(\S+)\s*$", raw)
            if name_m:
                current_name, current_line = name_m.group(1), lineno
                continue
            tag_m = re.match(r"^\s*newTag:\s*\"?([^\"\s]+)\"?\s*$", raw)
            if tag_m and current_name:
                entries.append((current_line, current_name, tag_m.group(1)))
                current_name = None
        mapped_infra = set()
        for lineno, name, tag in entries:
            if name in app_names:
                if tag != "APP_TAG_PLACEHOLDER":
                    errors.append(
                        f"{OPENSHIFT_KUSTOMIZATION}:{lineno}: app image {name!r} must keep "
                        f"newTag: APP_TAG_PLACEHOLDER (found {tag!r}) — the deploy script "
                        "substitutes it, and a real tag here would deploy stale bits"
                    )
                continue
            ref = f"{name}:{tag}"
            mapped_infra.add(ref)
            if ref not in infra_k8s:
                errors.append(
                    f"{OPENSHIFT_KUSTOMIZATION}:{lineno}: remaps {ref!r}, which is not in "
                    "deploy/images.manifest.txt with surface 'k8s'"
                )
        for ref in sorted(infra_k8s - mapped_infra):
            errors.append(
                f"{OPENSHIFT_KUSTOMIZATION}: does NOT remap {ref!r} — an air-gapped cluster "
                "would try to pull it from its upstream registry"
            )
        for name in sorted(app_names - {n for _, n, _ in entries}):
            errors.append(f"{OPENSHIFT_KUSTOMIZATION}: does NOT remap app image {name!r}")

    # ── 5. The OpenShift shell scripts must derive from the manifest ────────
    for rel in OPENSHIFT_SCRIPTS:
        path = repo_root / rel
        if not path.exists():
            errors.append(f"{rel}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        if "image-manifest.sh" not in text:
            errors.append(
                f"{rel}: does not source scripts/release/image-manifest.sh — its image list "
                "would drift from deploy/images.manifest.txt"
            )
        # A hardcoded default list (INFRA_IMAGES="postgres:16-alpine …") is the
        # exact drift this guard exists to prevent. A ${...} default is fine.
        for lineno, raw in enumerate(text.splitlines(), 1):
            if raw.lstrip().startswith("#"):
                continue  # usage/help text may legitimately show an example list
            m = re.search(r'INFRA_IMAGES="([^"]*)"', raw)
            if m and m.group(1) and "$" not in m.group(1):
                errors.append(
                    f"{rel}:{lineno}: hardcoded INFRA_IMAGES list — derive it from "
                    "deploy/images.manifest.txt via manifest_refs()"
                )

    return errors


def check_latest_tags(repo_root: Path) -> list[str]:
    """No floating/unpinned tags in any compose file.

    The existing ci.yml ``k8s-image-pin-check`` job only scans ``k8s/``. Compose
    had the same hole (``docker-compose.dev-lite.yml`` shipped ``minio:latest``
    and ``mc:latest``), and an unpinned tag makes an offline bundle
    unreproducible: whatever was on Docker Hub the day it was built is what the
    customer gets, forever, with no record of which version that was.
    """
    errors: list[str] = []
    for path in sorted(repo_root.glob("docker-compose*.yml")):
        rel = path.name
        for lineno, ref in compose_image_refs(path):
            # Drop ${...} spans, then judge what is left.
            bare = INTERPOLATION.sub("", ref)
            if bare.endswith(":"):
                continue  # tag comes from an env var — a deploy-time choice
            if not bare or bare in ("/", ""):
                continue
            tag = bare.rsplit(":", 1)[1] if ":" in bare.lstrip("/") else ""
            if tag == "latest":
                errors.append(f"{rel}:{lineno}: floating ':latest' tag: {ref}")
            elif not tag:
                errors.append(f"{rel}:{lineno}: unpinned image (no tag => :latest): {ref}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="repository root (default: two levels above this script)",
    )
    parser.add_argument(
        "--only",
        choices=("all", "drift", "latest"),
        default="all",
        help="'latest' runs only the floating-tag check (used by the ci.yml pin-check job "
        "so there is exactly one implementation of that rule); 'drift' skips it",
    )
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()

    errors: list[str] = []
    if args.only in ("all", "drift"):
        errors += check(repo_root)
    if args.only in ("all", "latest"):
        errors += check_latest_tags(repo_root)

    if errors:
        print("Image manifest drift detected:\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\ndeploy/images.manifest.txt is the single source of truth. Update it first, "
            "then bring every surface into line.",
            file=sys.stderr,
        )
        return 1

    if args.only == "latest":
        print("All compose image tags are pinned (no :latest, no untagged images).")
    else:
        print(
            "Image manifest: compose, k8s, openshift-artifactory and the mirror scripts all agree."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
