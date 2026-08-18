"""SCIM response attribute projection for User resources."""

import re
from copy import deepcopy
from dataclasses import dataclass

from app.models.schemas import SCIM_USER_SCHEMA

SCIM_PROJECTION_MAX_LENGTH = 2000
SCIM_PROJECTION_MAX_ATTRIBUTES = 100
_ATTRIBUTE_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)*$")
_ALWAYS_RETURNED = frozenset({"schemas", "id"})


class SCIMProjectionError(ValueError):
    """Raised when response projection query parameters are invalid."""


@dataclass(frozen=True)
class SCIMProjection:
    included: frozenset[str] | None = None
    excluded: frozenset[str] | None = None

    @property
    def active(self) -> bool:
        return self.included is not None or self.excluded is not None


def _canonical_path(raw_path: str) -> str:
    path = raw_path.strip()
    core_prefix = f"{SCIM_USER_SCHEMA}:"
    if path.casefold().startswith(core_prefix.casefold()):
        path = path[len(core_prefix):]
    if not _ATTRIBUTE_PATH.fullmatch(path):
        raise SCIMProjectionError(f"Invalid SCIM attribute path: {raw_path}")
    return path.casefold()


def _parse_paths(value: str | None) -> frozenset[str] | None:
    if value is None:
        return None
    if len(value) > SCIM_PROJECTION_MAX_LENGTH:
        raise SCIMProjectionError("SCIM attribute projection is too long")
    raw_paths = value.split(",")
    if not raw_paths or len(raw_paths) > SCIM_PROJECTION_MAX_ATTRIBUTES:
        raise SCIMProjectionError("SCIM attribute projection has too many paths")
    if any(not path.strip() for path in raw_paths):
        raise SCIMProjectionError("SCIM attribute projection contains an empty path")
    return frozenset(_canonical_path(path) for path in raw_paths)


def parse_scim_projection(
    attributes: str | None,
    excluded_attributes: str | None,
) -> SCIMProjection:
    """Validate mutually exclusive SCIM projection query parameters."""
    if attributes is not None and excluded_attributes is not None:
        raise SCIMProjectionError(
            "attributes and excludedAttributes cannot be used together"
        )
    return SCIMProjection(
        included=_parse_paths(attributes),
        excluded=_parse_paths(excluded_attributes),
    )


def _matching_key(document: dict, folded_name: str) -> str | None:
    return next((key for key in document if key.casefold() == folded_name), None)


def _project_complex(value: object, child_paths: set[str], *, include: bool) -> object:
    if isinstance(value, dict):
        result = deepcopy(value)
        for key in list(result):
            matched = key.casefold() in child_paths
            if (include and not matched) or (not include and matched):
                result.pop(key, None)
        return result
    if isinstance(value, list):
        return [
            _project_complex(item, child_paths, include=include)
            if isinstance(item, dict)
            else deepcopy(item)
            for item in value
        ]
    return deepcopy(value)


def project_scim_resource(resource: dict, projection: SCIMProjection) -> dict:
    """Apply top-level and one-level sub-attribute projection without mutation."""
    if not projection.active:
        return deepcopy(resource)

    if projection.included is not None:
        result: dict = {}
        for always in _ALWAYS_RETURNED:
            key = _matching_key(resource, always)
            if key is not None:
                result[key] = deepcopy(resource[key])
        top_paths = {path.split(".", 1)[0] for path in projection.included}
        for top_path in top_paths:
            key = _matching_key(resource, top_path)
            if key is None:
                continue
            if top_path in projection.included:
                result[key] = deepcopy(resource[key])
                continue
            children = {
                path.split(".", 1)[1]
                for path in projection.included
                if path.startswith(f"{top_path}.")
            }
            result[key] = _project_complex(resource[key], children, include=True)
        return result

    result = deepcopy(resource)
    assert projection.excluded is not None
    top_paths = {path.split(".", 1)[0] for path in projection.excluded}
    for top_path in top_paths:
        if top_path in _ALWAYS_RETURNED:
            continue
        key = _matching_key(result, top_path)
        if key is None:
            continue
        if top_path in projection.excluded:
            result.pop(key, None)
            continue
        children = {
            path.split(".", 1)[1]
            for path in projection.excluded
            if path.startswith(f"{top_path}.")
        }
        result[key] = _project_complex(result[key], children, include=False)
    return result
