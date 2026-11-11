"""Read-only checks through the candidate's real client entry points."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.live


def test_cli_ready(tmp_path):
    origin = os.environ["TL_API"]
    with urlopen(origin + "/health/ready", timeout=20) as response:
        expected = json.load(response)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "cli"),
        "TESTLOOKUP_URL": origin,
        "XDG_CONFIG_HOME": str(tmp_path),
        "PYTHONUTF8": "1",
    }
    env.pop("TESTLOOKUP_API_KEY", None)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "testlookup_cli.app",
            "health",
            "--profile",
            "e2e-" + uuid.uuid4().hex,
            "--output",
            "json",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert process.returncode == 0, "CLI health returned a nonzero exit"
    observed = json.loads(process.stdout)
    assert observed["status"] == expected["status"] == "ready"
    for service in ("postgres", "mongo", "redis"):
        assert (
            observed["checks"][service]["status"]
            == expected["checks"][service]["status"]
            == "ok"
        )


def test_mcp_stdio_public_health(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    origin = os.environ["TL_API"]
    with urlopen(origin + "/health/details", timeout=20) as response:
        expected = json.load(response)
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp/server.py")],
        cwd=str(tmp_path),
        env={"TESTLOOKUP_API_URL": origin, "PYTHONUTF8": "1"},
    )

    async def check():
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert "health_check" in names
            result = await session.call_tool("health_check", {})
            assert not result.is_error
            content = "\n".join(c.text for c in result.content if c.type == "text")
            assert "**Version:** " + expected["version"] in content
            for name in ("postgres", "mongo", "redis"):
                assert f"- {name}: ok" in content

    asyncio.run(asyncio.wait_for(check(), timeout=45))
