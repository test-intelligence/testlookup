from fastapi import Response


def _route(path: str, method: str):
    from app.routers.scim import router

    return next(
        route
        for route in router.routes
        if route.path == path and method in route.methods
    )


def test_public_scim_router_defaults_json_successes_to_scim_media_type():
    from app.core.scim_errors import SCIMJSONResponse
    from app.routers.scim import router

    assert router.default_response_class is SCIMJSONResponse
    response = SCIMJSONResponse({"schemas": []})
    assert response.media_type == "application/scim+json"
    assert response.headers["content-type"] == "application/scim+json"


def test_admin_token_router_keeps_fastapi_default_response_class():
    from app.core.scim_errors import SCIMJSONResponse
    from app.routers.scim import token_router

    assert token_router.default_response_class is not SCIMJSONResponse


def test_public_scim_delete_remains_bodyless():
    route = _route("/api/v1/scim/v2/Users/{user_id}", "DELETE")

    assert route.response_class is Response
    response = route.response_class(status_code=204)
    assert response.body == b""
    assert "content-type" not in response.headers


def test_public_scim_json_routes_inherit_scim_response_class():
    from app.core.scim_errors import SCIMJSONResponse

    for method, path in (
        ("GET", "/api/v1/scim/v2/Users"),
        ("GET", "/api/v1/scim/v2/Users/{user_id}"),
        ("POST", "/api/v1/scim/v2/Users"),
        ("PUT", "/api/v1/scim/v2/Users/{user_id}"),
        ("PATCH", "/api/v1/scim/v2/Users/{user_id}"),
    ):
        assert _route(path, method).response_class is SCIMJSONResponse
