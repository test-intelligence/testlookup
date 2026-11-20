"""The cloud deployment examples suggest the application's own access-token lifetime.

The rule: an example environment file an operator copies for a production
deployment (Cloud Run, the GCP VM stack) does not suggest a longer access
token than the application default. A stolen access token works until it
expires; the examples used to set 1440 minutes (a day) where the default is
30. The line stays in each file, so an operator still sees the setting.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ["infra/cloudrun/backend.env.example", ".env.gcp-vm.example"]
DEFAULT_MINUTES = Settings.model_fields["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"].default


def _suggested(relative_path: str) -> list[int]:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    return [int(v) for v in re.findall(r"^JWT_ACCESS_TOKEN_EXPIRE_MINUTES=(\d+)\s*$", text, re.MULTILINE)]


def test_the_application_default_is_half_an_hour():
    assert DEFAULT_MINUTES == 30


@pytest.mark.parametrize("relative_path", EXAMPLES)
def test_the_example_names_the_setting_once(relative_path):
    assert len(_suggested(relative_path)) == 1


@pytest.mark.parametrize("relative_path", EXAMPLES)
def test_the_example_does_not_suggest_a_longer_access_token(relative_path):
    assert _suggested(relative_path) == [DEFAULT_MINUTES]
