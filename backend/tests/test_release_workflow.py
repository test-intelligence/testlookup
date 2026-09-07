"""Release workflow contracts live in test_verified_release_promotion."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_is_promotion_only():
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "require-verified-sha.yml" in text
    assert "release_manifest.py" in text
    assert "docker/build-push-action" not in text
    assert "crane tag" in text
