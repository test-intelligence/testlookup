"""No overlay renders the placeholder ``testlookup-secrets`` Secret.

``k8s/base`` listed ``secrets.yaml``, whose ``data`` holds ``<base64-...>``
placeholders, so eight overlays rendered it (OSS review 2026-11-12, L-8). The
API server refuses those values (they are not base64), so ``kubectl apply -k``
failed on every one of them; and ``scripts/deploy-k8s.sh`` asks for the real
Secret first and then runs ``kubectl apply -k``, which tried to overwrite it.
The file stays as a template that kustomize never applies, and the docs say to
create the Secret out of band.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
K8S = ROOT / "k8s"
PLACEHOLDER = re.compile(r"<[^<>]+>")


def _kustomizations() -> list[Path]:
    found = sorted(K8S.rglob("kustomization.yaml"))
    assert len(found) >= 11, f"expected the base and ten overlays, found {len(found)}"
    return found


def _resources(kustomization: Path) -> list[Path]:
    doc = yaml.safe_load(kustomization.read_text(encoding="utf-8")) or {}
    listed = [kustomization.parent / entry for entry in doc.get("resources") or []]
    return [path for path in listed if path.suffix in (".yaml", ".yml") and path.is_file()]


def test_the_base_does_not_apply_the_secret_template():
    base = K8S / "base" / "kustomization.yaml"
    assert (K8S / "base" / "secrets.yaml") not in _resources(base)
    # The template itself stays: the docs and two tests read its keys.
    template = (K8S / "base" / "secrets.yaml").read_text(encoding="utf-8")
    assert "kind: Secret" in template and PLACEHOLDER.search(template)


def test_no_kustomization_applies_a_secret_with_placeholder_values():
    offenders = []
    for kustomization in _kustomizations():
        for resource in _resources(kustomization):
            for doc in yaml.safe_load_all(resource.read_text(encoding="utf-8")):
                if not isinstance(doc, dict) or doc.get("kind") != "Secret":
                    continue
                values = {**(doc.get("data") or {}), **(doc.get("stringData") or {})}
                bad = sorted(key for key, value in values.items() if PLACEHOLDER.search(str(value)))
                if bad:
                    offenders.append(f"{resource.relative_to(ROOT).as_posix()}: {bad}")
    assert not offenders, f"these Secrets would be applied with placeholder values: {offenders}"


def test_no_overlay_still_patches_the_removed_secret():
    """A delete patch for a resource the base no longer renders is dead text."""
    stale = [k.relative_to(ROOT).as_posix() for k in _kustomizations()
             if re.search(r"kind:\s*Secret\s*\n\s*metadata:\s*\n\s*name:\s*testlookup-secrets",
                          k.read_text(encoding="utf-8"))]
    assert not stale, f"these kustomizations still patch testlookup-secrets: {stale}"


def test_the_kubernetes_guide_says_to_create_the_secret_first():
    guide = (ROOT / "docs" / "operations" / "deploy-kubernetes.md").read_text(encoding="utf-8")
    assert "kustomize does not apply it" in guide
