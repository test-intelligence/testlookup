"""How secrets stay out of a project export (W24 ADM-05, contract §2.2 S-1 … S-6).

The export reads NO secret store (``secret_refs`` / ``app_settings`` / ``api_keys``
are never selected — they are not EXPORTED tables). What remains are secrets a
person TYPED into an exported column; this module scrubs every exported row:

* **typed secret flags** (S-1): ``has_secret`` / ``has_pat`` are written ``false``
  (the secret itself lives in ``secret_refs`` and is never read);
* **secret-named columns** (R-2): a column named like a secret
  (``redaction_service.SENSITIVE_KEYS`` / ``is_log_secret_name``, the camelCase
  split, a ``secret_service.SECRET_FIELDS`` field name) → ``[REDACTED]``;
* **team channel targets** (S-3): a Slack / Teams target IS the webhook secret →
  ``[REDACTED]``; an ``email`` target is a team address and is kept verbatim;
* **URLs** (S-4): :func:`strip_url_credentials` — userinfo removed, then
  ``http_exchange_redaction.redact_url``; a ``SECRET_PATH_HOSTS`` host keeps only
  ``/[REDACTED]`` as its path and ``[REDACTED]`` as every query value;
* **JSON / ARRAY columns** (S-5): :func:`scrub_json`;
* **free text** (S-6): every ``String`` / ``Text`` column not named in
  ``TEXT_PASS_EXEMPT`` → the text pass (``http_exchange_redaction.text_pass`` plus
  the JWT and secret-host rules below), settled to a fixed point.

Every function is idempotent: scrubbing a scrubbed row changes nothing.
PURE: no I/O, no session, no logging of content.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Optional, Protocol

from sqlalchemy import JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.models.postgres import Base
from app.services.http_exchange_redaction import URL_PARTS_RE, redact_url, text_pass
from app.services.redaction_service import REDACTED, SENSITIVE_KEYS, is_log_secret_name
from app.services.secret_service import SECRET_FIELDS

#: Hosts whose URL PATH (and query) is the credential: an incoming-webhook URL. Matched on the
#: host itself or as a suffix (``<tenant>.webhook.office.com``).
SECRET_PATH_HOSTS: tuple[str, ...] = (
    "hooks.slack.com",
    "outlook.office.com",
    "webhook.office.com",
    "discord.com",
    "discordapp.com",
    "chat.googleapis.com",
)

#: A JSON container nested deeper than this (the top-level value is depth 1) → ``[REDACTED]``.
JSON_DEPTH_MAX = 20

#: Columns whose values are URLs (S-4).
URL_COLUMNS: Mapping[str, frozenset[str]] = {
    "webhook_subscriptions": frozenset({"target_url"}),
    "github_integrations": frozenset({"api_base_url"}),
    "gitlab_integrations": frozenset({"base_url"}),
    "releases": frozenset({"external_url"}),
}

#: Typed secret flags written as ``false`` (S-1): the secret is in ``secret_refs``, never exported.
FORCED_FALSE: Mapping[str, frozenset[str]] = {
    "webhook_subscriptions": frozenset({"has_secret"}),
    "github_integrations": frozenset({"has_pat"}),
    "gitlab_integrations": frozenset({"has_pat"}),
}

#: ``team_notification_channels.channel_type`` values whose ``target`` is kept (S-3); every other
#: type's target (``slack``, ``teams`` and anything unknown) is ``[REDACTED]`` — fail closed.
KEPT_CHANNEL_TARGET_TYPES: frozenset[str] = frozenset({"email"})

#: ``String`` / ``Text`` columns that stay BYTE-EXACT (ids, enums, names, fingerprints, slugs and
#: patterns). Every other text column of an exported table gets the text pass (S-6). The four pattern
#: columns are documented risk R-3: a token typed INTO a pattern travels.
TEXT_PASS_EXEMPT: Mapping[str, frozenset[str]] = {
    "projects": frozenset({"name", "slug", "jira_project_key", "splunk_index", "ocp_namespace", "jenkins_job_pattern"}),
    "test_suites": frozenset({"name"}),
    "managed_test_cases": frozenset({
        "title", "test_type", "priority", "severity", "feature_area", "suite_name", "status",
        "needs_update_reason", "automation_status", "test_fingerprint", "dup_fingerprint",
        "last_execution_status", "faithfulness_evaluator",
    }),
    "test_case_versions": frozenset({
        "title", "test_type", "priority", "severity", "feature_area", "suite_name", "automation_status",
        "test_fingerprint", "status", "change_type", "source",
    }),
    "test_case_requirement_links": frozenset({"requirement_key", "requirement_key_norm", "requirement_title", "link_type"}),
    "canonical_test_cases": frozenset({"test_fingerprint", "test_name", "class_name", "status", "source", "review_tag"}),
    "test_plans": frozenset({"name", "status"}),
    "test_plan_items": frozenset({"priority_override", "execution_status"}),
    "test_strategies": frozenset({"name", "version_label", "status", "ai_model_used"}),
    "releases": frozenset({
        "name", "version", "status", "release_type", "sort_key", "target_environment", "source_system", "external_id",
    }),
    "release_phases": frozenset({"name", "phase_type", "status"}),
    "release_gate_policies": frozenset({"name"}),
    "release_attribution_rules": frozenset({"name", "match_field", "match_pattern", "target_release_name"}),
    "team_notification_channels": frozenset({"team_name", "channel_type"}),
    "webhook_subscriptions": frozenset({"name", "disabled_reason"}),
    "github_integrations": frozenset({"repo_owner", "repo_name", "auth_kind", "pr_comment_mode"}),
    "gitlab_integrations": frozenset({"project_path", "mr_comment_mode"}),
    "service_ownership_rules": frozenset({"match_type", "match_pattern", "service_name", "team_name"}),
    "project_workflow_defaults": frozenset({"base", "workflow_id"}),
    "workflow_definitions": frozenset({"workflow_id", "name", "base", "status", "eval_verdict"}),
    "agent_configs": frozenset({"agent_id", "mode"}),
    "run_cadences": frozenset({"suite_name", "suite_key", "mode", "cron", "timezone"}),
    "classification_hints": frozenset({"match_field", "pattern", "category"}),
    "project_llm_quota": frozenset({"period_type", "at_cap_action"}),
}

_SECRET_FIELD_NAMES: frozenset[str] = frozenset(name for names in SECRET_FIELDS.values() for name in names)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_URL_PARTS = re.compile(URL_PARTS_RE)
#: A JWT: three base64url segments, the header always starting ``eyJ`` (base64 of ``{"``). The text
#: pass's own JWT rule needs ``eyJ`` on the payload too and 20+ characters per segment.
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")
_SECRET_HOST_IN_TEXT = re.compile(
    r"(https?://(?:[A-Za-z0-9-]+\.)*(?:"
    + "|".join(re.escape(host) for host in SECRET_PATH_HOSTS)
    + r"))(?::\d{1,5})?(?:[/?][^\s'\"<>]*)",
    re.IGNORECASE,
)
_SETTLE_ROUNDS = 6


class _HasTable(Protocol):
    """The part of ``project_export_entities.EntitySpec`` the scrubber reads."""

    @property
    def table(self) -> str: ...


# ── names, text, urls ────────────────────────────────────────────────────────

def is_secret_name(name: object) -> bool:
    """A column / JSON key named like a secret: ``SENSITIVE_KEYS``, the log rule (``is_log_secret_name``:
    exact names + the ``_token`` / ``_secret`` … suffixes) or a ``SECRET_FIELDS`` field name — on the name
    lower-cased with ``-`` read as ``_`` and on its camelCase split (``apiKey`` → ``api_key``)."""
    if not isinstance(name, str) or not name:
        return False
    plain = name.lower().replace("-", "_")
    snake = _CAMEL_BOUNDARY.sub("_", name).lower().replace("-", "_")
    return any(
        candidate in SENSITIVE_KEYS or candidate in _SECRET_FIELD_NAMES or is_log_secret_name(candidate)
        for candidate in (plain, snake)
    )


def _settle(text: str, step: Callable[[str], str]) -> str:
    for _ in range(_SETTLE_ROUNDS):
        nxt = step(text)
        if nxt == text:
            return nxt
        text = nxt
    return text


def _text_once(text: str) -> str:
    text = text_pass(text)
    text = _JWT.sub(REDACTED, text)
    return _SECRET_HOST_IN_TEXT.sub(lambda match: f"{match.group(1)}/{REDACTED}", text)


def scrub_text(text: Optional[str]) -> Optional[str]:
    """The free-text pass (S-6): ``text_pass`` (secret-named pairs, token shapes, ``redact_text``), a JWT
    of any segment length, and a ``SECRET_PATH_HOSTS`` URL's path / query → ``[REDACTED]``. Idempotent."""
    if not isinstance(text, str) or not text:
        return text
    return _settle(text, _text_once)


def _host_of(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):
        return host[: host.find("]") + 1].lower() if "]" in host else host.lower()
    return host.split(":", 1)[0].lower().rstrip(".")


def _is_secret_path_host(host: str) -> bool:
    return any(host == secret or host.endswith("." + secret) for secret in SECRET_PATH_HOSTS)


def _strip_once(url: str) -> str:
    match = _URL_PARTS.match(url)
    if match is None:  # pragma: no cover - the pattern matches every string
        return url
    scheme, netloc, path, query = match.group(1, 2, 3, 4)
    out = f"{scheme}:" if scheme is not None else ""
    if netloc is not None:
        host_part = netloc.rsplit("@", 1)[-1]          # userinfo removed entirely
        out += "//" + host_part
        if _is_secret_path_host(_host_of(host_part)):
            out += "/" + REDACTED
            if query:
                out += "?" + "&".join(
                    f"{part.partition('=')[0]}={REDACTED}" if part.partition("=")[1] else REDACTED
                    for part in query.split("&")
                )
            return out
    out += path or ""
    if query is not None:
        out += "?" + query
    redacted, _markers = redact_url(out)
    return redacted


def strip_url_credentials(url: Optional[str]) -> Optional[str]:
    """A URL without credentials (S-4): the userinfo removed; ``redact_url``'s query denylist, token-shaped
    path segments and dropped fragment; a ``SECRET_PATH_HOSTS`` host keeps only ``/[REDACTED]`` and
    ``[REDACTED]`` query values. Idempotent. A non-string (or empty) value is returned as it is."""
    if not isinstance(url, str) or not url:
        return url
    return _settle(url.strip(), _strip_once)


# ── json ─────────────────────────────────────────────────────────────────────

def _walk(value: Any, depth: int) -> Any:
    if isinstance(value, (dict, list, tuple)) and depth > JSON_DEPTH_MAX:
        return REDACTED
    if isinstance(value, dict):
        return {
            key: (REDACTED if value_ is not None and is_secret_name(key) else _walk(value_, depth + 1))
            for key, value_ in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_walk(item, depth + 1) for item in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_json(value: Any) -> Any:
    """A JSON / ARRAY value (S-5): a key named like a secret (:func:`is_secret_name`) → ``[REDACTED]``;
    every string leaf → :func:`scrub_text`; lists walked IN FULL; a container deeper than
    ``JSON_DEPTH_MAX`` → ``[REDACTED]``. Keys are kept. Idempotent."""
    return _walk(value, 1)


# ── rows ─────────────────────────────────────────────────────────────────────

_KINDS_CACHE: dict[str, dict[str, str]] = {}


def _kinds_for(table: str) -> dict[str, str]:
    """``{column: "json" | "text" | "other"}`` for a table of ``Base.metadata`` (an unknown table → ``{}``)."""
    cached = _KINDS_CACHE.get(table)
    if cached is not None:
        return cached
    kinds: dict[str, str] = {}
    meta = Base.metadata.tables.get(table)
    for column in meta.columns if meta is not None else ():
        kind = column.type
        if isinstance(kind, (JSON, JSONB, ARRAY)):
            kinds[column.name] = "json"
        elif isinstance(kind, (String, Text)):
            kinds[column.name] = "text"
        else:
            kinds[column.name] = "other"
    _KINDS_CACHE[table] = kinds
    return kinds


def _scrub_value(table: str, column: str, value: Any, kind: str, row: Mapping[str, Any]) -> Any:
    if value is None:
        return None
    if column in FORCED_FALSE.get(table, ()):
        return False
    if table == "team_notification_channels" and column == "target":
        return value if row.get("channel_type") in KEPT_CHANNEL_TARGET_TYPES else REDACTED
    if is_secret_name(column):
        return False if isinstance(value, bool) else REDACTED
    if column in URL_COLUMNS.get(table, ()):
        return strip_url_credentials(value) if isinstance(value, str) else value
    if kind == "json" or isinstance(value, (dict, list, tuple)):
        return scrub_json(value)
    if kind == "text" and isinstance(value, str) and column not in TEXT_PASS_EXEMPT.get(table, ()):
        return scrub_text(value)
    return value


def scrub_row(spec: _HasTable, row: Mapping[str, Any]) -> dict[str, Any]:
    """One exported row with every §2.2 rule applied (the caller has already removed ``spec.drop``).

    The rules are chosen by the column's TYPE in ``Base.metadata`` (text / JSON) and its NAME, so a new
    free-text or JSON column of an exported table is scrubbed by default (R-2); only the columns named in
    ``TEXT_PASS_EXEMPT`` stay byte-exact; a column the metadata does not know is read as text (fail closed).
    Returns a NEW dict; the input is not modified. Idempotent."""
    table = spec.table
    kinds = _kinds_for(table)
    return {
        column: _scrub_value(table, column, value, kinds.get(column, "text"), row)
        for column, value in row.items()
    }
