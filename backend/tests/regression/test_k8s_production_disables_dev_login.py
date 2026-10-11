"""Every Kubernetes overlay that runs ``APP_ENV=production`` turns dev login off.

``DEV_AUTO_LOGIN_ENABLED`` defaults to true in ``app.core.config`` (local
development), and the production startup check refuses to start with it on.
``k8s/base`` sets ``APP_ENV=production`` but, until the OSS release, set nothing
for dev login, so the API of every overlay that did not override it (prod,
openshift, the managed-cloud overlays) refused to start. The base now carries
an explicit off that every overlay inherits.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "k8s" / "base" / "configmap.yaml"
OVERLAYS = REPO / "k8s" / "overlays"


def _value(text: str, key: str) -> str | None:
    match = re.search(rf'^\s*{key}:\s*"([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else None


def test_the_base_turns_dev_login_off_in_production():
    text = BASE.read_text(encoding="utf-8")
    assert _value(text, "APP_ENV") == "production"
    assert _value(text, "DEV_AUTO_LOGIN_ENABLED") == "false"


def test_no_production_overlay_turns_it_back_on():
    checked = 0
    for kustomization in sorted(OVERLAYS.glob("*/kustomization.yaml")):
        text = kustomization.read_text(encoding="utf-8")
        if _value(text, "APP_ENV") in (None, "production"):
            checked += 1
            assert _value(text, "DEV_AUTO_LOGIN_ENABLED") in (None, "false"), kustomization.parent.name
    assert checked >= 5


def test_the_settings_default_is_what_the_base_overrides():
    from app.core.config import Settings

    assert Settings.model_fields["DEV_AUTO_LOGIN_ENABLED"].default is True
