"""DEV-02: the deployment serves the `testlookup` CLI as a wheel.

Onboarding said "The CLI is not published to PyPI — it ships in the repo" and
offered only ``pip install -e cli/`` (a clone). The image's build context now
stages a wheel built from ``cli/`` (CI and ``deploy-homelab.sh``) under
``client_sdks/cli/``; ``GET /api/v1/sdk/cli/info`` names it, ``GET
/api/v1/sdk/cli/<wheel>`` serves it with its real file name (pip needs one), and
``GET /api/v1/sdk`` lists "CLI". Only a staged wheel name is ever served.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.routers import sdk


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setattr(sdk, "SDK_BASE_PATH", str(tmp_path))
    (tmp_path / "cli").mkdir()
    return tmp_path / "cli"


def _body(response) -> dict:
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_info_names_the_newest_wheel(staged):
    for version in ("0.1.0", "0.10.0", "0.9.2"):
        (staged / f"testlookup_cli-{version}-py3-none-any.whl").write_bytes(b"PK")
    (staged / "other_pkg-9.9.9-py3-none-any.whl").write_bytes(b"PK")  # never offered
    info = _body(await sdk.cli_info())
    assert info == {
        "available": True, "version": "0.10.0", "filename": "testlookup_cli-0.10.0-py3-none-any.whl",
        "download_url": "/api/v1/sdk/cli/testlookup_cli-0.10.0-py3-none-any.whl",
    }


@pytest.mark.asyncio
async def test_without_a_staged_wheel_it_says_so(staged):
    assert _body(await sdk.cli_info()) == {"available": False, "version": None, "filename": None, "download_url": None}
    listed = {s["lang"]: s for s in _body(await sdk.list_sdks())["sdks"]}
    assert listed["cli"]["available"] is False and listed["cli"]["download_url"] is None
    with pytest.raises(HTTPException) as exc:
        await sdk.download_sdk("cli")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_the_wheel_is_served_by_its_exact_name_and_listed(staged):
    name = "testlookup_cli-0.1.0-py3-none-any.whl"
    (staged / name).write_bytes(b"PK\x03\x04wheel")
    response = await sdk.download_cli_wheel(name)
    assert response.path.endswith(name)
    assert f'filename="{name}"' in response.headers["content-disposition"]
    listed = {s["lang"]: s for s in _body(await sdk.list_sdks())["sdks"]}
    assert listed["cli"] == {
        "lang": "cli", "label": "CLI", "filename": name, "download_url": f"/api/v1/sdk/cli/{name}", "available": True,
    }
    via_lang = await sdk.download_sdk("cli")
    assert via_lang.path.endswith(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "../pyproject.toml", "..%2Fpyproject.toml", "testlookup_cli-0.1.0-py3-none-any.whl/../../x",
    "other_pkg-9.9.9-py3-none-any.whl", "testlookup_cli-0.2.0-py3-none-any.whl", "testlookup_reporter.py",
])
async def test_anything_but_a_staged_wheel_name_is_404(staged, bad):
    (staged / "testlookup_cli-0.1.0-py3-none-any.whl").write_bytes(b"PK")
    (staged / "other_pkg-9.9.9-py3-none-any.whl").write_bytes(b"PK")
    with pytest.raises(HTTPException) as exc:
        await sdk.download_cli_wheel(bad)
    assert exc.value.status_code == 404
