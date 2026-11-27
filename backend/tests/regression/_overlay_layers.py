"""Local sibling-overlay inheritance for static deployment contracts."""
from pathlib import Path

import yaml


def overlay_layers(path: Path, ancestors: tuple[Path, ...] = ()) -> list[Path]:
    """Return inherited overlays before the current overlay, rejecting cycles."""
    path = path.resolve()
    assert path not in ancestors, "cyclic overlay inheritance"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    layers = []
    for resource in document.get("resources", []):
        parent = (path.parent / resource / "kustomization.yaml").resolve()
        if parent.is_file() and parent.parent.parent == path.parent.parent:
            layers.extend(overlay_layers(parent, (*ancestors, path)))
    return [*layers, path]
