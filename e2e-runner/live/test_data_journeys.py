"""Exercise the test library, independent review and versioned plan execution."""

import os

import httpx
import pytest

pytestmark = [pytest.mark.live, pytest.mark.ephemeral]


def _response(response, expected):
    assert response.status_code == expected, (
        f"{response.request.method} {response.request.url.path}: "
        f"HTTP {response.status_code}, request {response.headers.get('x-request-id')}"
    )
    return response.json()


def test_managed_case_lifecycle(project):
    reviewer, owned, name = project
    review_user = _response(reviewer.get("auth/me"), 200)
    with httpx.Client(base_url=reviewer.base_url, timeout=30) as author:
        token = os.environ.get("TL_TOKEN_QA_ENGINEER")
        if not token:
            token = _response(
                author.post("auth/dev-login", params={"role": "qa_engineer"}), 200
            )["access_token"]
        author.headers["Authorization"] = "Bearer " + token
        user = _response(author.get("auth/me"), 200)
        assert (user["username"], user["role"]) == (
            os.environ["TL_USER_QA_ENGINEER"],
            "QA_ENGINEER",
        )
        assert user["id"] != review_user["id"]
        _response(
            reviewer.post(
                f"projects/{owned}/members",
                json={"user_id": user["id"], "role": user["role"]},
            ),
            201,
        )
        settings = _response(
            reviewer.put("projects/" + owned, json={"require_case_review": True}), 200
        )
        assert settings["require_case_review"] is True
        cases = "test-management/cases"
        case = _response(
            author.post(cases, json={"project_id": owned, "title": name}), 201
        )
        case_path = f"{cases}/{case['id']}"
        assert (case["author_id"], case["status"], case["version"]) == (
            user["id"],
            "draft",
            1,
        )
        history = _response(author.get(case_path + "/history"), 200)
        # Under review-on policy a draft cannot skip review. Compare persisted
        # history with the snapshot from immediately before the invalid action.
        _response(
            author.post(
                case_path + "/transition",
                json={"action": "activate", "expected_version": 1},
            ),
            409,
        )
        assert _response(author.get(case_path + "/history"), 200) == history
        requirement = name.upper()
        link = _response(
            author.post(
                case_path + "/requirements",
                json={"requirement_key": requirement, "link_type": "verifies"},
            ),
            201,
        )
        assert (link["test_case_id"], link["requirement_key"]) == (
            case["id"],
            requirement,
        )
        steps = [
            (author, user, "request_review", "review_requested"),
            (reviewer, review_user, "claim_review", "under_review"),
            (reviewer, review_user, "approve", "approved"),
            (reviewer, review_user, "activate", "active"),
        ]
        for version, (actor, identity, action, status) in enumerate(steps, 1):
            updated = _response(
                actor.post(
                    case_path + "/transition",
                    json={
                        "action": action,
                        "expected_version": version,
                        "notes": name,
                    },
                ),
                200,
            )
            assert (updated["status"], updated["version"]) == (status, version + 1)
            persisted = _response(author.get(case_path), 200)
            assert (persisted["status"], persisted["version"]) == (status, version + 1)
            record = _response(author.get(case_path + "/history"), 200)[0]
            assert (
                record["version"],
                record["status"],
                record["change_type"],
                record["changed_by_id"],
            ) == (version + 1, status, action, identity["id"])
        reviews = _response(author.get(case_path + "/reviews"), 200)
        assert [
            (r["status"], r["reviewer_id"], r["requested_by_id"]) for r in reviews
        ] == [("approved", review_user["id"], user["id"])]
        plans = "test-management/plans"
        plan = _response(
            reviewer.post(plans, json={"project_id": owned, "name": name}), 201
        )
        plan_path = f"{plans}/{plan['id']}"
        item = _response(
            reviewer.post(plan_path + "/items", json={"test_case_id": case["id"]}), 201
        )
        executed = _response(
            author.patch(
                f"{plan_path}/items/{item['id']}/execute",
                json={"execution_status": "passed", "execution_notes": name},
            ),
            200,
        )
        assert (executed["executed_case_version"], executed["executed_by_id"]) == (
            5,
            user["id"],
        )
        items = _response(author.get(plan_path + "/items"), 200)
        assert [
            (i["test_case_id"], i["executed_case_version"], i["execution_status"])
            for i in items
        ] == [(case["id"], 5, "passed")]
        recorded_plan = _response(author.get(plan_path), 200)
        coverage = _response(
            author.get(
                "test-management/traceability/gaps", params={"project_id": owned}
            ),
            200,
        )
        assert coverage["summary"]["known"] == coverage["summary"]["verified"] == 1
        assert (
            _response(author.get(case_path + "/requirements"), 200)["items"][0]["id"]
            == link["id"]
        )
        _response(
            reviewer.put("projects/" + owned, json={"require_case_review": False}), 200
        )
        direct = _response(
            author.post(cases, json={"project_id": owned, "title": name + "-direct"}),
            201,
        )
        activated = _response(
            author.post(
                f"{cases}/{direct['id']}/transition",
                json={"action": "activate", "expected_version": 1},
            ),
            200,
        )
        assert (activated["status"], activated["version"]) == ("active", 2)
        assert (
            recorded_plan["total_cases"],
            recorded_plan["executed_cases"],
            recorded_plan["passed_cases"],
        ) == (1, 1, 1)
