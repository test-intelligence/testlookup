"""Project membership as observed through the supported user-facing clients."""

import asyncio
import json
import os

import pytest
from mcp import ClientSession
from mcp.client.sse import sse_client
from typer.testing import CliRunner
from testlookup_cli import config
from testlookup_cli.app import app

pytestmark = [pytest.mark.live, pytest.mark.ephemeral]


async def read_mcp_projects(auth):
    async with sse_client(
        os.environ["TL_MCP"] + "/sse", headers={"Authorization": auth}
    ) as transport:
        async with ClientSession(*transport) as session:
            await session.initialize()
            result = await session.read_resource("testlookup://projects")
            return json.loads(result.contents[0].text)


def test_project_memberships(project, role_client, tmp_path, monkeypatch):
    _, owned, name = project
    admin, _ = role_client("ADMIN")
    roster = f"projects/{owned}/members"
    cli_dir = tmp_path / "profiles"
    monkeypatch.setattr(config, "CONFIG_DIR", cli_dir)
    monkeypatch.setattr(config, "PROFILES_FILE", cli_dir / "profiles.json")
    identities = set()
    for role in ("VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"):
        client, identity = role_client(role)
        uid = identity["id"]
        assert uid not in identities
        identities.add(uid)
        members = admin.get(roster)
        assert members.status_code == 200
        if uid not in {m["user_id"] for m in members.json()}:
            grant = admin.post(roster, json={"user_id": uid, "role": role})
            assert grant.status_code == 201
        membership = client.get(roster)
        assert membership.status_code == 200
        own = [m for m in membership.json() if m["user_id"] == uid]
        assert [(m["project_id"], m["role"]) for m in own] == [(owned, role)]
        projects = client.get("projects")
        assert projects.status_code == 200
        assert [p["name"] for p in projects.json() if p["id"] == owned] == [name]

        # The temporary CLI profile uses the same principal as the API and MCP.
        config.save_profile(
            role,
            {
                "url": os.environ["TL_API"],
                "auth_type": "jwt",
                "access_token": client.headers["Authorization"].split(" ", 1)[1],
            },
        )
        try:
            cli = CliRunner().invoke(
                app, ["projects", "get", owned, "--profile", role, "--output", "json"]
            )
            assert cli.exit_code == 0, f"CLI project read failed for {role}"
            assert json.loads(cli.stdout)["id"] == owned
        finally:
            config.delete_profile(role)
        accessible = asyncio.run(
            asyncio.wait_for(
                read_mcp_projects(client.headers["Authorization"]), timeout=30
            )
        )
        assert [p["name"] for p in accessible if p["id"] == owned] == [name]
        with client.stream("GET", f"projects/{owned}/activity/stream") as activity:
            assert activity.status_code == 200
            assert activity.headers["content-type"].startswith("text/event-stream")
        # The activity export has a documented lead-on-this-project minimum.
        export = client.get(
            f"projects/{owned}/activity/export", params={"format": "ndjson"}
        )
        if role in ("QA_LEAD", "ADMIN"):
            assert export.status_code == 200
            rows = [
                json.loads(line) for line in export.text.splitlines() if line.strip()
            ]
            assert any(
                row["event_type"] == "project.created" and row["entity_id"] == owned
                for row in rows
            )
        else:
            assert export.status_code == 403
