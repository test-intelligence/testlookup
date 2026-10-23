"""A project's Splunk index and OpenShift namespace are validated at save (W10 INT-03, D-6).

The Splunk index is interpolated into SPL (``search index=<it> ...``) and the
namespace into the OpenShift API path, so a value that is not SPL-safe / not a
DNS-1123 label is refused with a fixed sentence (422) when a QA lead saves the
project, and an empty value on Edit clears the field (stored ``""``, read as
unset: the deployment's index / namespace apply). The same shapes guard the
deployment-wide Integrations form (``IntegrationsConfigUpdate``).

The 422s go through a real FastAPI route so the ``msg`` a page renders is
pinned exactly: a ``PydanticCustomError`` carries the sentence verbatim, where
a ``ValueError`` would have prefixed it with "Value error, ".
"""
from __future__ import annotations

from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.integration_values import (
    INTEGRATION_URL_SENTENCE,
    JIRA_DOMAIN_SENTENCE,
    OCP_NAMESPACE_SENTENCE,
    SPLUNK_INDEX_SENTENCE,
    integration_url_problem,
    valid_namespace,
    valid_pod_name,
    valid_splunk_index,
)
from app.models.schemas import IntegrationsConfigUpdate, ProjectCreate, ProjectUpdate

BAD_INDEXES = ["main | delete", "a b", '"x"', "-x", "x" * 81, "idx*", "a=b", "(x)", "x'y"]
BAD_NAMESPACES = ["QA", "qa_payments", "-qa", "qa-", "q" * 64, "qa testing", "qa.payments"]


def _app() -> TestClient:
    app = FastAPI()

    @app.post("/projects")
    async def create(payload: ProjectCreate) -> dict:
        return payload.model_dump(exclude_none=True)

    @app.put("/projects")
    async def update(payload: ProjectUpdate) -> dict:
        return payload.model_dump(exclude_none=True)

    @app.put("/integrations")
    async def integrations(payload: IntegrationsConfigUpdate) -> dict:
        return payload.model_dump(exclude_none=True)

    return TestClient(app)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return _app()


def _errors(response) -> list[tuple[list, str]]:
    assert response.status_code == 422, response.text
    return [(item["loc"], item["msg"]) for item in response.json()["detail"]]


# ── Splunk index ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("index", ["payments", "pay_ments-1", "Main", "a", "x" * 80])
def test_an_spl_safe_index_is_accepted(client, index):
    assert client.put("/projects", json={"splunk_index": index}).json() == {"splunk_index": index}
    created = client.post("/projects", json={"name": "Payments", "slug": "payments", "splunk_index": index})
    assert created.json()["splunk_index"] == index


@pytest.mark.parametrize("index", BAD_INDEXES)
def test_an_unsafe_index_is_refused_with_the_sentence(client, index):
    assert _errors(client.put("/projects", json={"splunk_index": index})) == [
        (["body", "splunk_index"], SPLUNK_INDEX_SENTENCE),
    ]
    assert _errors(client.post("/projects", json={"name": "Pay", "slug": "pay", "splunk_index": index})) == [
        (["body", "splunk_index"], SPLUNK_INDEX_SENTENCE),
    ]


def test_the_sentence_has_no_value_error_prefix(client):
    [(_, msg)] = _errors(client.put("/projects", json={"splunk_index": "main | delete"}))
    assert not msg.startswith("Value error")
    assert msg == "Splunk index may use letters, digits, '_' and '-' only (1-80 characters, starting with a letter or digit)."


def test_an_index_is_stripped_before_it_is_judged(client):
    assert client.put("/projects", json={"splunk_index": "  payments  "}).json() == {"splunk_index": "payments"}


# ── OpenShift namespace ─────────────────────────────────────────────────────


@pytest.mark.parametrize("namespace", ["qa-payments", "qa", "a1", "q" * 63])
def test_a_dns_label_namespace_is_accepted(client, namespace):
    assert client.put("/projects", json={"ocp_namespace": namespace}).json() == {"ocp_namespace": namespace}


@pytest.mark.parametrize("namespace", BAD_NAMESPACES)
def test_a_namespace_that_is_not_a_dns_label_is_refused(client, namespace):
    assert _errors(client.put("/projects", json={"ocp_namespace": namespace})) == [
        (["body", "ocp_namespace"], OCP_NAMESPACE_SENTENCE),
    ]
    assert _errors(client.post("/projects", json={"name": "Pay", "slug": "pay", "ocp_namespace": namespace})) == [
        (["body", "ocp_namespace"], OCP_NAMESPACE_SENTENCE),
    ]


def test_both_fields_are_reported_together(client):
    errors = _errors(client.put("/projects", json={"splunk_index": "a b", "ocp_namespace": "QA"}))
    assert sorted(errors) == sorted([
        (["body", "splunk_index"], SPLUNK_INDEX_SENTENCE),
        (["body", "ocp_namespace"], OCP_NAMESPACE_SENTENCE),
    ])


# ── Clearing ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cleared", ["", "   "])
def test_an_empty_value_on_edit_clears_the_field(client, cleared):
    # exclude_none keeps "", so the PUT applies it (routers/projects.py).
    assert client.put("/projects", json={"splunk_index": cleared, "ocp_namespace": cleared}).json() == {
        "splunk_index": "", "ocp_namespace": "",
    }


def test_an_empty_value_on_create_is_stored_as_none():
    created = ProjectCreate(name="Payments", slug="payments", splunk_index="", ocp_namespace="  ")
    assert created.splunk_index is None and created.ocp_namespace is None


def test_none_keeps_the_stored_value():
    update = ProjectUpdate(name="Renamed")
    assert "splunk_index" not in update.model_dump(exclude_none=True)
    assert "ocp_namespace" not in update.model_dump(exclude_none=True)


def test_jenkins_job_pattern_is_kept_and_described_as_unused():
    assert ProjectUpdate(jenkins_job_pattern="team-*").jenkins_job_pattern == "team-*"
    for model in (ProjectCreate, ProjectUpdate):
        assert model.model_fields["jenkins_job_pattern"].description == (
            "Stored only; TestLookup does not use it (W10 D22)."
        )


# ── The deployment-wide Integrations form ───────────────────────────────────


@pytest.mark.parametrize("field, value, sentence", [
    ("splunk_base_url", "splunk.corp:8089", INTEGRATION_URL_SENTENCE),
    ("splunk_base_url", "ftp://splunk.corp", INTEGRATION_URL_SENTENCE),
    ("splunk_base_url", "https://admin:pw@splunk.corp:8089", INTEGRATION_URL_SENTENCE),
    ("ocp_api_url", "https://", INTEGRATION_URL_SENTENCE),
    ("jira_domain", "https://x.atlassian.net/", JIRA_DOMAIN_SENTENCE),
    ("jira_domain", "x.atlassian.net/browse", JIRA_DOMAIN_SENTENCE),
    ("ocp_default_namespace", "QA Testing", OCP_NAMESPACE_SENTENCE),
])
def test_the_integrations_form_refuses_a_bad_shape(client, field, value, sentence):
    assert _errors(client.put("/integrations", json={field: value})) == [(["body", field], sentence)]


@pytest.mark.parametrize("field, value, stored", [
    ("splunk_base_url", " https://splunk.corp:8089 ", "https://splunk.corp:8089"),
    ("ocp_api_url", "http://10.0.0.5:6443", "http://10.0.0.5:6443"),
    ("jira_domain", "acme.atlassian.net", "acme.atlassian.net"),
    ("jira_domain", "jira.corp:8443", "jira.corp:8443"),
    ("ocp_default_namespace", "qa-testing", "qa-testing"),
    ("splunk_base_url", "", ""),           # hands the key back to the environment
    ("ocp_default_namespace", "  ", ""),
])
def test_the_integrations_form_strips_and_keeps_an_empty_value(client, field, value, stored):
    assert client.put("/integrations", json={field: value}).json() == {field: stored}


def test_the_deprecated_slack_channel_is_still_accepted():
    payload = IntegrationsConfigUpdate(slack_default_channel="#x")
    assert payload.model_dump(exclude_none=True) == {"slack_default_channel": "#x"}
    assert IntegrationsConfigUpdate.model_fields["slack_default_channel"].deprecated


def test_a_validation_error_names_its_own_type():
    with pytest.raises(ValidationError) as caught:
        ProjectUpdate(splunk_index="a b")
    [error] = caught.value.errors()
    assert error["type"] == "splunk_index" and error["msg"] == SPLUNK_INDEX_SENTENCE


# ── The pure helpers the services use at use-time ───────────────────────────


@pytest.mark.parametrize("value, ok", [
    ("payments", True), ("main | delete", False), ("", False), (None, False), (5, False),
])
def test_valid_splunk_index(value, ok):
    assert valid_splunk_index(value) is ok


@pytest.mark.parametrize("value, ok", [
    ("qa-payments", True), ("QA", False), ("", False), (None, False), ("../x", False),
])
def test_valid_namespace(value, ok):
    assert valid_namespace(value) is ok


@pytest.mark.parametrize("value, ok", [
    ("payment-runner-7d9f", True), ("runner.v2-1", True), ("../secrets", False), ("Pod", False),
    ("", False), (None, False), ("a" * 254, False), ("a" * 253, True), ("x\n", False),
])
def test_valid_pod_name(value, ok):
    assert valid_pod_name(value) is ok


@pytest.mark.parametrize("url, problem", [
    ("https://splunk.corp:8089", None),
    ("http://10.0.0.5", None),
    ("splunk.corp", INTEGRATION_URL_SENTENCE),
    ("https://u@splunk.corp", INTEGRATION_URL_SENTENCE),
    ("https://[::1", INTEGRATION_URL_SENTENCE),
    ("javascript:alert(1)", INTEGRATION_URL_SENTENCE),
])
def test_integration_url_problem(url: str, problem: Optional[str]):
    assert integration_url_problem(url) == problem
