"""Render the independent data-preservation fixture used by upgrade drivers.

The rendered program runs inside a disposable backend container/pod. It uses
only HTTP and datastore clients, never product models or report parsers. Its
single stdout record contains owned identifiers and public checksums; sessions
and credentials stay in process memory.
"""

from __future__ import annotations

import json
import re
import textwrap


def render(action, state=None, expected_revision=None):
    """Return backend-side Python for seed, check, or candidate_mark.

    ``seed`` returns the baseline in ``result["state"]``. Check that baseline
    under the candidate, then pass it to ``candidate_mark``. The latter returns
    a new state with candidate-only sentinels. Checking that new state after
    snapshot restoration requires those sentinels to be absent.
    """
    if action not in {"seed", "check", "candidate_mark"}:
        raise ValueError("Unknown upgrade fixture action")
    if action != "seed" and not isinstance(state, dict):
        raise ValueError("A previous sanitized fixture state is required")
    if expected_revision is not None and not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        raise ValueError("Expected revision must be a full lowercase source SHA")
    if action == "check" and expected_revision is None:
        raise ValueError("A preservation check must identify its expected revision")
    payload = json.dumps({"action": action, "state": state, "expected_revision": expected_revision}, separators=(",", ":"))
    return "import json\n_REQUEST = json.loads(" + repr(payload) + ")\n" + textwrap.dedent(_PROGRAM)


_PROGRAM = r'''
import copy
import hashlib
import os
import secrets
import sys
import time
import uuid

import httpx

_stage = "environment"
_requests = []
_api = "http://127.0.0.1:8000"
_golden = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="upgrade-preservation" tests="4" failures="1" errors="1" skipped="1">
<testcase classname="upgrade.persisted" name="baseline_pass" time="0.01"/>
<testcase classname="upgrade.persisted" name="baseline_fail" time="0.02"><failure message="upgrade fixture assertion">expected fixture failure</failure></testcase>
<testcase classname="upgrade.persisted" name="baseline_skip" time="0.03"><skipped message="upgrade fixture skip"/></testcase>
<testcase classname="upgrade.persisted" name="baseline_error" time="0.04"><error message="upgrade fixture setup error">expected fixture error</error></testcase>
</testsuite></testsuites>"""
_expected_leaves = {"baseline_pass": "PASSED", "baseline_fail": "FAILED", "baseline_skip": "SKIPPED", "baseline_error": "BROKEN"}
_count_fields = ("total_tests", "passed_tests", "failed_tests", "skipped_tests", "broken_tests")


class PolicyUnsupported(AssertionError):
    """The required baseline policy oracle cannot be exercised."""


def stage(value):
    global _stage
    _stage = value


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fields(value, names):
    return {name: value.get(name) for name in names}


def request(client, method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if len(_requests) < 200:
        _requests.append({"method": method, "path": path, "status": response.status_code,
                          "expected": expected, "request_id": response.headers.get("x-request-id")})
    if response.status_code != expected:
        raise AssertionError("http_status")
    return response


def profile(client, role):
    stage("identity." + role)
    session = request(client, "POST", "/api/v1/auth/dev-login", params={"role": role.lower()}).json()
    token = session["access_token"]
    user = request(client, "GET", "/api/v1/auth/me", headers={"Authorization": "Bearer " + token}).json()
    assert user["username"] == role.lower() and user["role"] == role
    assert str(uuid.UUID(user["id"])) == user["id"]
    return token, fields(user, ("id", "username", "role"))


def reset_initial_admin(client):
    stage("admin.first_sign_in")
    dev_session = request(client, "POST", "/api/v1/auth/dev-login", params={"role": "admin"}).json()
    existing = request(client, "GET", "/api/v1/auth/me",
        headers={"Authorization": "Bearer " + dev_session["access_token"]}).json()
    assert (existing["username"], existing["role"]) == ("admin", "ADMIN")
    if existing.get("must_change_password") is False:
        return False
    assert existing.get("must_change_password") is True
    session = request(client, "POST", "/api/v1/auth/login", data={
        "username": "admin", "password": os.environ["INITIAL_ADMIN_PASSWORD"]}).json()
    assert session["must_change_password"] is True
    headers = {"Authorization": "Bearer " + session["access_token"]}
    user = request(client, "GET", "/api/v1/auth/me", headers=headers).json()
    assert (user["username"], user["role"]) == ("admin", "ADMIN")
    password = secrets.token_urlsafe(36) + "aA1!"
    request(client, "POST", "/api/v1/auth/first-time-reset", expected=204, headers=headers,
            json={"new_password": password, "confirm_password": password})
    final = request(client, "POST", "/api/v1/auth/login", data={"username": "admin", "password": password}).json()
    assert final["must_change_password"] is False
    return True


def s3_client():
    import boto3
    from botocore.client import Config
    scheme = "https" if os.environ.get("MINIO_USE_SSL", "false").lower() == "true" else "http"
    endpoint = os.environ["MINIO_ENDPOINT"]
    return boto3.client("s3", endpoint_url=endpoint if "://" in endpoint else scheme + "://" + endpoint,
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"], aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        region_name="us-east-1", config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                              connect_timeout=10, read_timeout=30))


def mongo_client():
    from pymongo import MongoClient
    uri = os.environ.get("MONGO_URI") or "mongodb://" + os.environ["MONGO_HOST"] + ":" + os.environ.get("MONGO_PORT", "27017")
    return MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000, socketTimeoutMS=30000)


def put_object(fixture_id, kind):
    stage("object." + kind + ".write")
    client = s3_client()
    bucket = os.environ.get("MINIO_BUCKET_NAME", "test-telemetry")
    key = "e2e-upgrade/" + fixture_id + "/" + kind + ".bin"
    content = canonical({"fixture_id": fixture_id, "kind": kind, "purpose": "disposable upgrade snapshot oracle"})
    metadata = {"fixture": fixture_id, "snapshot": kind}
    client.put_object(Bucket=bucket, Key=key, Body=content, ContentType="application/octet-stream",
                      CacheControl="private, max-age=17", Metadata=metadata)
    response = client.get_object(Bucket=bucket, Key=key)
    stored = response["Body"].read()
    assert stored == content
    attributes = object_attributes(response)
    stage("object." + kind + ".content_type")
    assert isinstance(attributes.get("ContentType"), str)
    requested = {"ContentType": "application/octet-stream", "CacheControl": "private, max-age=17",
                 "Metadata": metadata}
    capabilities = {name: {"returned": name in attributes, "matches_requested": attributes.get(name) == value}
                    for name, value in requested.items()}
    # The upgrade oracle freezes the actual baseline, including normalized or
    # absent optional fields. Capability differences at first write are explicit
    # evidence and cannot be mistaken for data lost during a later transition.
    stage("object." + kind + ".etag")
    assert isinstance(attributes.get("ETag"), str) and attributes["ETag"]
    stage("object." + kind + ".bucket_acl")
    return {"bucket": bucket, "key": key, "sha256": sha(content), "size": len(content),
            "attributes": attributes, "requested_field_capabilities": capabilities,
            "bucket_acl_sha256": bucket_acl_hash(client, bucket),
            "object_acl_supported": False,
            "object_acl_capability_source": "https://github.com/versity/versitygw/wiki/Incompatibilities-With-AWS-S3"}


def object_attributes(response):
    names = ("ContentType", "ETag", "Metadata", "CacheControl", "ChecksumCRC32", "ChecksumCRC32C",
             "ChecksumCRC64NVME", "ChecksumSHA1", "ChecksumSHA256", "ChecksumType")
    return {name: response[name] for name in names if name in response}


def bucket_acl_hash(client, bucket):
    # S3 owner IDs can contain an access key. Keep only a canonical hash of the
    # ACL, never the raw owner, grants, response headers or credential values.
    acl = client.get_bucket_acl(Bucket=bucket)
    return sha(canonical({name: value for name, value in acl.items() if name != "ResponseMetadata"}))


def put_mongo(fixture_id, project_id, kind):
    stage("mongo." + kind + ".write")
    database = os.environ.get("MONGO_DB", "testlookup_logs")
    collection = "e2e_upgrade_snapshot_markers"
    document = {"_id": fixture_id + "-" + kind, "fixture_id": fixture_id, "project_id": project_id, "kind": kind}
    with mongo_client() as client:
        client[database][collection].insert_one(document)
        assert client[database][collection].find_one({"_id": document["_id"]}) == document
    return {"database": database, "collection": collection, "id": document["_id"], "document": document,
            "sha256": sha(canonical(document))}


def read_leaves(client, run_id):
    result = request(client, "GET", "/api/v1/runs/" + run_id + "/tests", params={"page": 1, "size": 200}).json()
    assert result["total"] == len(_expected_leaves) and len(result["items"]) == result["total"]
    leaves = [fields(value, ("id", "test_run_id", "test_name", "suite_name", "class_name", "status", "duration_ms")) for value in result["items"]]
    assert {leaf["test_name"]: leaf["status"] for leaf in leaves} == _expected_leaves
    assert len({leaf["id"] for leaf in leaves}) == len(leaves)
    for leaf in leaves:
        assert str(uuid.UUID(leaf["id"])) == leaf["id"] and leaf["test_run_id"] == run_id
    return sorted(leaves, key=lambda value: value["id"])


def memberships(client, project_id):
    result = request(client, "GET", "/api/v1/projects/" + project_id + "/members").json()
    return sorted([fields(value, ("id", "user_id", "project_id", "role", "username", "source")) for value in result], key=lambda value: value["id"])


def policy_fields(value):
    return fields(value, ("id", "project_id", "version", "name", "description", "rules", "is_active", "is_draft", "created_by", "activated_by"))


def seed(client):
    admin_reset_performed = reset_initial_admin(client)
    admin_token, admin = profile(client, "ADMIN")
    viewer_token, viewer = profile(client, "VIEWER")
    client.headers["Authorization"] = "Bearer " + admin_token
    fixture_id = uuid.uuid4().hex
    name = "e2e-upgrade-" + fixture_id
    stage("project.seed")
    project = request(client, "POST", "/api/v1/projects", expected=201,
        json={"name": name, "slug": name, "description": "Disposable prior-release preservation fixture " + fixture_id}).json()
    project = fields(project, ("id", "name", "slug", "description", "is_active"))
    stage("membership.seed")
    member = request(client, "POST", "/api/v1/projects/" + project["id"] + "/members", expected=201,
                     json={"user_id": viewer["id"], "role": "VIEWER"}).json()
    member = fields(member, ("id", "user_id", "project_id", "role", "username", "source"))
    assert member["user_id"] == viewer["id"] and member["role"] == "VIEWER"
    request(client, "GET", "/api/v1/projects/" + project["id"], headers={"Authorization": "Bearer " + viewer_token})
    stage("ingestion.seed")
    receipt = request(client, "POST", "/api/v1/ingest/file", expected=202,
        data={"project_id": project["id"], "build_number": name, "run_ai": "false", "format": "junit"},
        files={"file": ("upgrade-golden.xml", _golden, "application/xml")}).json()
    deadline = time.monotonic() + 180
    while True:
        status = request(client, "GET", "/api/v1/ingest/uploads/" + receipt["task_id"]).json()["state"]
        if status == "succeeded":
            break
        assert status != "failed" and time.monotonic() < deadline
        time.sleep(0.5)
    stage("ingestion.persisted")
    run = request(client, "GET", "/api/v1/runs/" + receipt["run_id"]).json()
    assert run["project_id"] == project["id"] and run["build_number"] == name
    assert fields(run, _count_fields) == {"total_tests": 4, "passed_tests": 1, "failed_tests": 1, "skipped_tests": 1, "broken_tests": 1}
    run = fields(run, ("id", "project_id", "build_number", *_count_fields))
    leaves = read_leaves(client, run["id"])
    stage("policy.seed")
    created = client.post("/api/v1/release-gate-policies", json={"project_id": project["id"], "name": name + "-policy",
        "description": "Preserve this published project policy across the upgrade",
        "rules": {"schema_version": 1, "thresholds": {"go_threshold": 17, "no_go_threshold": 61, "pass_rate_minimum": 91,
                                                       "pass_rate_hard_floor_factor": 0.65}}})
    _requests.append({"method": "POST", "path": "/api/v1/release-gate-policies", "status": created.status_code,
                      "expected": 201, "request_id": created.headers.get("x-request-id")})
    if created.status_code in (404, 405):
        raise PolicyUnsupported()
    assert created.status_code == 201
    policy_id = created.json()["id"]
    policy = request(client, "POST", "/api/v1/release-gate-policies/" + policy_id + "/publish").json()
    assert policy["is_active"] is True and policy["is_draft"] is False
    policy = {"supported": True, "value": policy_fields(policy)}
    effective = request(client, "GET", "/api/v1/release-gate-policies/effective/" + project["id"]).json()
    assert policy_fields(effective) == policy["value"]
    return {"schema_version": 1, "fixture_id": fixture_id, "admin_password_reset_complete": True,
        "admin_reset_performed_by_fixture": admin_reset_performed,
        "admin": admin, "viewer": viewer, "project": project, "membership": member,
        "memberships": memberships(client, project["id"]), "run": run, "leaves": leaves, "policy": policy,
        "golden_sha256": sha(_golden), "object": put_object(fixture_id, "baseline"),
        "mongo": put_mongo(fixture_id, project["id"], "baseline")}


def verify_object(value, absent=False):
    from botocore.exceptions import ClientError
    stage("object.absent" if absent else "object.preserved")
    try:
        client = s3_client()
        response = client.get_object(Bucket=value["bucket"], Key=value["key"])
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if absent and code in {"NoSuchKey", "404", "NotFound"}:
            return
        raise AssertionError("object_missing_or_unreadable") from None
    assert not absent
    content = response["Body"].read()
    assert len(content) == value["size"] and sha(content) == value["sha256"]
    assert object_attributes(response) == value["attributes"]
    assert value["object_acl_supported"] is False
    assert bucket_acl_hash(client, value["bucket"]) == value["bucket_acl_sha256"]


def verify_mongo(value, absent=False):
    stage("mongo.absent" if absent else "mongo.preserved")
    with mongo_client() as client:
        document = client[value["database"]][value["collection"]].find_one({"_id": value["id"]})
    if absent:
        assert document is None
    else:
        assert document == value["document"] and sha(canonical(document)) == value["sha256"]


def check(client, state):
    assert state["schema_version"] == 1 and state["admin_password_reset_complete"] is True
    admin_token, admin = profile(client, "ADMIN")
    viewer_token, viewer = profile(client, "VIEWER")
    assert admin == state["admin"] and viewer == state["viewer"]
    client.headers["Authorization"] = "Bearer " + admin_token
    project_id = state["project"]["id"]
    stage("project.preserved")
    project = request(client, "GET", "/api/v1/projects/" + project_id).json()
    assert fields(project, state["project"]) == state["project"]
    request(client, "GET", "/api/v1/projects/" + project_id, headers={"Authorization": "Bearer " + viewer_token})
    stage("membership.preserved")
    assert memberships(client, project_id) == state["memberships"]
    assert state["membership"] in state["memberships"]
    stage("run.preserved")
    run = request(client, "GET", "/api/v1/runs/" + state["run"]["id"]).json()
    assert fields(run, state["run"]) == state["run"]
    stage("leaves.preserved")
    assert read_leaves(client, state["run"]["id"]) == state["leaves"]
    stage("policy.preserved")
    policy = state["policy"]
    if policy.get("supported") is not True:
        raise PolicyUnsupported()
    value = request(client, "GET", "/api/v1/release-gate-policies/" + policy["value"]["id"]).json()
    effective = request(client, "GET", "/api/v1/release-gate-policies/effective/" + project_id).json()
    assert policy_fields(value) == policy["value"] and policy_fields(effective) == policy["value"]
    verify_object(state["object"])
    verify_mongo(state["mongo"])
    if state.get("candidate_mark"):
        marked = state["candidate_mark"]
        stage("candidate_project.absent")
        request(client, "GET", "/api/v1/projects/" + marked["project"]["id"], expected=404)
        verify_object(marked["object"], absent=True)
        verify_mongo(marked["mongo"], absent=True)
    return {"project": True, "case_ids_and_counts": True, "membership_and_viewer_access": True,
        "policy": True, "baseline_object_checksum": True, "baseline_object_metadata_and_acl_hash": True,
        "baseline_mongo_document": True,
        "candidate_sentinels_removed": bool(state.get("candidate_mark"))}


def candidate_mark(client, state):
    assert not state.get("candidate_mark")
    check(client, state)
    stage("candidate_project.create")
    name = "e2e-upgrade-candidate-" + state["fixture_id"]
    project = request(client, "POST", "/api/v1/projects", expected=201,
                      json={"name": name, "slug": name, "description": "Must disappear after restoring the prior snapshot"}).json()
    project = fields(project, ("id", "name", "slug"))
    request(client, "GET", "/api/v1/projects/" + project["id"])
    state["candidate_mark"] = {"project": project, "object": put_object(state["fixture_id"], "candidate"),
        "mongo": put_mongo(state["fixture_id"], project["id"], "candidate")}
    return state


def main():
    assert os.environ.get("APP_ENV") == "development"
    assert os.environ.get("DEV_AUTO_LOGIN_ENABLED", "").lower() == "true"
    with httpx.Client(base_url=_api, timeout=30, follow_redirects=False, trust_env=False) as client:
        stage("health.revision")
        version = request(client, "GET", "/health/version").json()
        revision = version["build"]["revision"]
        if _REQUEST["expected_revision"] is not None:
            assert revision == _REQUEST["expected_revision"]
        request(client, "GET", "/health/ready")
        state = copy.deepcopy(_REQUEST["state"])
        action = _REQUEST["action"]
        if action == "seed":
            state = seed(client)
            checks = check(client, state)
        elif action == "candidate_mark":
            state = candidate_mark(client, state)
            checks = {"candidate_project_present": True, "candidate_object_present": True, "candidate_mongo_document_present": True}
        else:
            checks = check(client, state)
        return {"ok": True, "action": action, "expected_revision": _REQUEST["expected_revision"],
                "observed_revision": revision, "state": state, "checks": checks, "requests": _requests}


try:
    _result = main()
except Exception as _error:
    _result = {"ok": False, "action": _REQUEST["action"], "stage": _stage,
               "error_type": type(_error).__name__,
               "error_code": "policyUnsupported" if isinstance(_error, PolicyUnsupported) else "oracleMismatch",
               "requests": _requests}
print("UPGRADE_JSON=" + json.dumps(_result, separators=(",", ":")))
sys.exit(0 if _result["ok"] else 1)
'''
