"""
Regression test: the multipart ``POST /api/v1/ingest/file`` form must bound
``branch``/``commit_hash``/``release_name`` to the same lengths the JSON
``IngestPayload`` schema (and the backing DB columns) use.

Before this, those three form fields were declared ``Form(None)`` with no
length cap while their siblings (``ci_provider``/``ci_repo``/... ) were all
bounded. An over-long value therefore sailed past the router's validation and
only failed at insert time in the Celery worker — off the request path, so the
caller got a 202 and never learned the run failed. The JSON path already
rejected the same input with a clean 422; this test pins the file path to match.
"""
import inspect

import pytest

pytest.importorskip("fastapi")
# app.routers.ingest imports app.core.deps, which needs python-jose + asyncpg.
# Present in the Docker test image; skip cleanly on a bare local checkout.
pytest.importorskip("jose")
pytest.importorskip("asyncpg")

from annotated_types import MaxLen  # noqa: E402


def _form_max_length(param_name: str) -> int | None:
    """The ``max_length`` a Form field on ``ingest_file`` declares, or None.

    FastAPI/pydantic v2 store the constraint in the Form default's ``metadata``
    list as an ``annotated_types.MaxLen`` entry, not as a top-level attribute.
    """
    from app.routers.ingest import ingest_file

    default = inspect.signature(ingest_file).parameters[param_name].default
    for constraint in getattr(default, "metadata", []):
        if isinstance(constraint, MaxLen):
            return constraint.max_length
    return None


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("branch", 255),
        ("commit_hash", 64),
        ("release_name", 255),
    ],
)
def test_ingest_file_form_fields_are_bounded(field: str, expected: int):
    assert _form_max_length(field) == expected


def test_ingest_file_bounds_match_json_ingest_payload():
    """The file-upload caps must not drift from the JSON schema's caps."""
    from app.models.schemas import IngestPayload

    fields = IngestPayload.model_fields
    for name in ("branch", "commit_hash", "release_name"):
        schema_max = next(
            (m.max_length for m in fields[name].metadata if isinstance(m, MaxLen)),
            None,
        )
        assert _form_max_length(name) == schema_max, name
