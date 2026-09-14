"""E1.4: the agent API's Postman collection and curl reference, generated from OpenAPI.

The committed files under ``architecture/api/`` must equal what the live
schema generates; CI runs the same comparison as ``--check``. These tests pin:

* the selection: exactly the catalog, invocation and review routes;
* the shape: Postman v2.1, bearer auth, every variable declared;
* the details a client needs: an Idempotency-Key on invoke, and a ticket rather
  than a bearer token on the event stream;
* the guardrail: a new body route fails generation until it has an example.
"""
from __future__ import annotations

import json
import re

import pytest

from app.services import agent_api_docs as docs

EXPECTED = {
    ("GET", "/api/v1/agents/catalog"),
    ("GET", "/api/v1/agents/catalog/{agent_id}"),
    ("GET", "/api/v1/agents/invocations/{invocation_id}"),
    ("POST", "/api/v1/agents/invocations/{invocation_id}/cancel"),
    ("GET", "/api/v1/agents/invocations/{invocation_id}/events"),
    ("POST", "/api/v1/agents/invocations/{invocation_id}/events/ticket"),
    ("POST", "/api/v1/agents/invocations/{invocation_id}/retry"),
    ("POST", "/api/v1/agents/{agent_id}/invoke"),
    ("GET", "/api/v1/projects/{project_id}/reviews"),
    ("GET", "/api/v1/reviews/{review_id}"),
    ("POST", "/api/v1/reviews/{review_id}/accept"),
    ("POST", "/api/v1/reviews/{review_id}/reject"),
}


@pytest.fixture(scope="module")
def spec():
    from app.main import app

    return app.openapi()


def _section(markdown: str, heading: str) -> str:
    """One operation's section: up to the next operation or folder heading."""
    return re.split(r"\n##+ ", markdown.split(heading + "\n", 1)[1], maxsplit=1)[0]


def test_the_selection_is_exactly_the_agent_and_review_api(spec):
    assert {(o["method"], o["path"]) for o in docs.select_operations(spec)} == EXPECTED


def test_the_committed_files_match_the_schema(spec):
    for path, text in docs.render(spec).items():
        assert path.exists(), f"{path} is missing: run `python -m app.services.agent_api_docs` in backend/"
        assert path.read_text(encoding="utf-8") == text, (
            f"{path.name} is stale: run `python -m app.services.agent_api_docs` in backend/"
        )


def test_every_selected_route_is_in_both_outputs(spec):
    collection = docs.build_collection(spec)
    markdown = docs.build_curl_markdown(spec)
    requests = {
        (item["request"]["method"], "/" + "/".join(item["request"]["url"]["path"]))
        for folder in collection["item"] for item in folder["item"]
    }
    for operation in docs.select_operations(spec):
        assert (operation["method"], re.sub(r"\{(\w+)\}", r"{{\1}}", operation["path"])) in requests
        assert f"### {operation['method']} {operation['path']}" in markdown


def test_the_collection_is_postman_v2_1_with_bearer_auth_and_declares_every_variable(spec):
    collection = docs.build_collection(spec)
    assert collection["info"]["schema"].endswith("/v2.1.0/collection.json")
    assert collection["auth"] == {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]}
    declared = {v["key"] for v in collection["variable"]}
    used = set(re.findall(r"\{\{(\w+)\}\}", json.dumps(collection["item"])))
    assert used <= declared, f"undeclared variables: {used - declared}"
    assert [f["name"] for f in collection["item"]] == ["Catalog", "Invocations", "Reviews"]


def test_invoke_sends_an_idempotency_key_and_the_catalog_input_shape(spec):
    collection = docs.build_collection(spec)
    invoke = next(
        item for folder in collection["item"] for item in folder["item"]
        if item["request"]["url"]["path"][-1] == "invoke"
    )
    headers = {h["key"]: h for h in invoke["request"]["header"]}
    assert headers["Idempotency-Key"]["value"] == "{{$guid}}" and not headers["Idempotency-Key"].get("disabled")
    assert headers["X-API-Key"]["disabled"] is True
    body = json.loads(invoke["request"]["body"]["raw"])
    assert body["input"] == {"agent_id": "{{agent_id}}", "payload": {"test_run_id": "{{test_run_id}}"}}
    assert body["config_overrides"] == {"model": {"tier": "slm"}}

    block = _section(docs.build_curl_markdown(spec), "### POST /api/v1/agents/{agent_id}/invoke")
    assert '"$BASE_URL/api/v1/agents/$AGENT_ID/invoke"' in block
    assert 'Idempotency-Key: $(uuidgen)' in block
    assert '"test_run_id": "$TEST_RUN_ID"' in block and block.rstrip().endswith("```")


def test_the_event_stream_uses_its_ticket_not_a_bearer_token(spec):
    block = _section(docs.build_curl_markdown(spec), "### GET /api/v1/agents/invocations/{invocation_id}/events")
    assert "curl -sN" in block and "ticket=$TICKET" in block
    assert "Authorization" not in block


def test_optional_query_parameters_are_listed_but_not_forced(spec):
    markdown = docs.build_curl_markdown(spec)
    block = _section(markdown, "### GET /api/v1/projects/{project_id}/reviews")
    assert "`state`" in block and "`limit`" in block
    assert "state=" not in block.split("```bash", 1)[1]


def test_a_body_route_without_an_example_fails_generation():
    spec = {"paths": {"/api/v1/reviews/{review_id}/escalate": {"post": {"summary": "Escalate", "requestBody": {}}}}}
    with pytest.raises(ValueError, match="EXAMPLE_BODIES"):
        docs.build_collection(spec)
    with pytest.raises(ValueError, match="EXAMPLE_BODIES"):
        docs.build_curl_markdown(spec)


def test_check_fails_on_stale_files_and_passes_once_regenerated(tmp_path, monkeypatch, spec):
    monkeypatch.setattr(docs, "COLLECTION_PATH", tmp_path / "api" / "c.json")
    monkeypatch.setattr(docs, "CURL_PATH", tmp_path / "api" / "c.md")
    monkeypatch.setattr(docs, "load_spec", lambda: spec)

    assert docs.main(["--check"]) == 1, "missing files are stale"
    assert docs.main([]) == 0
    assert docs.main(["--check"]) == 0
    (tmp_path / "api" / "c.md").write_text("hand edited\n", encoding="utf-8")
    assert docs.main(["--check"]) == 1


def test_generation_is_deterministic(spec):
    assert docs.render(spec) == docs.render(spec)
