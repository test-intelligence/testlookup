"""Create only the monitored run's synthetic project and restricted API key."""
from __future__ import annotations

import json
import uuid

import httpx


def seed(client):
    def request(method, path, expected, *, token=None, **kwargs):
        headers = {"Authorization": "Bearer " + token} if token else {}
        response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code != expected:
            raise RuntimeError("seed_route_failed")
        return response.json()

    admin = request("POST", "auth/dev-login", 200, params={"role": "admin"})["access_token"]
    engineer = request("POST", "auth/dev-login", 200, params={"role": "qa_engineer"})["access_token"]
    identity = request("GET", "auth/me", 200, token=engineer)
    if identity.get("role") != "QA_ENGINEER":
        raise RuntimeError("seed_principal_mismatch")
    name = "release-signal-" + uuid.uuid4().hex[:12]
    project = request("POST", "projects", 201, token=admin, json={"name": name, "slug": name})
    project_id = str(uuid.UUID(project["id"]))
    request("POST", f"projects/{project_id}/members", 201, token=admin, json={"user_id": identity["id"], "role": "QA_ENGINEER"})
    scopes = ["project:write", "stream:write"]
    key = request("POST", "keys", 201, token=admin, json={
        "name": name, "project_id": project_id, "target_user_id": identity["id"], "scopes": scopes, "expires_days": 1,
    })
    if str(key.get("project_id")) != project_id or set(key.get("scopes", [])) != set(scopes) or not key.get("raw_key"):
        raise RuntimeError("seed_key_boundary_mismatch")
    return {"project_id": project_id, "api_key": key["raw_key"]}


def main():
    try:
        with httpx.Client(base_url="http://127.0.0.1:8000/api/v1/", timeout=10, trust_env=False, follow_redirects=False) as client:
            result = seed(client)
        # Only the orchestrator captures stdout into its private scratch area.
        print(json.dumps(result))
        return 0
    except Exception:
        print("Synthetic monitored seed failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
