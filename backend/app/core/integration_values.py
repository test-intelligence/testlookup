"""The shapes of the values an operator or a QA lead types for an integration (W10).

Pure: imports nothing from ``app``, so the Pydantic schemas (validation at
save) and the services / tools (validation at use) read ONE definition of each
rule and ONE sentence per refusal. A stored value that predates a rule is not
re-validated on read; it is refused where it would be used.

* ``SPLUNK_INDEX_RE``: an index name safe to interpolate into SPL (no space,
  quote, pipe, ``=``, ``*`` or parenthesis).
* ``DNS_LABEL_RE``: a Kubernetes / OpenShift namespace (DNS-1123 label).
* ``DNS_SUBDOMAIN_RE``: a pod name (DNS-1123 subdomain).
* ``JIRA_HOST_RE``: a bare Jira host, optionally with a port (the shape of
  ``defect_jira_service._JIRA_HOST_RE``).

Every regex is used with ``fullmatch``.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

SPLUNK_INDEX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")          # SPL-safe: no space, quote, pipe, =, *, (, )
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")     # a Kubernetes namespace
DNS_SUBDOMAIN_RE = re.compile(r"(?=.{1,253}$)[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?")   # a pod name
JIRA_HOST_RE = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?")            # the shape of defect_jira_service._JIRA_HOST_RE

SPLUNK_INDEX_SENTENCE = (
    "Splunk index may use letters, digits, '_' and '-' only (1-80 characters, starting with a letter or digit)."
)
OCP_NAMESPACE_SENTENCE = (
    "OpenShift namespace must be a DNS label: lowercase letters, digits and '-', at most 63 "
    "characters, starting and ending with a letter or digit."
)
JIRA_DOMAIN_SENTENCE = (
    "Jira domain must be a host name such as your-company.atlassian.net, without https:// or a path."
)
INTEGRATION_URL_SENTENCE = (
    "Enter a URL that starts with http:// or https:// and names a host, with no user name or password in it."
)
TARGET_NOT_ALLOWED = "Target host is not allowed"


def integration_url_problem(url: str) -> Optional[str]:
    """``None`` when ``url`` is http(s), names a host and carries no userinfo;
    else ``INTEGRATION_URL_SENTENCE``. No DNS: the host check is
    ``url_safety.egress_block_reason`` (async)."""
    if not isinstance(url, str):
        return INTEGRATION_URL_SENTENCE
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return INTEGRATION_URL_SENTENCE
    if parts.scheme.lower() not in ("http", "https") or not host:
        return INTEGRATION_URL_SENTENCE
    if "@" in parts.netloc:
        return INTEGRATION_URL_SENTENCE
    return None


def _full(pattern: "re.Pattern[str]", value: Any) -> bool:
    return isinstance(value, str) and bool(value) and pattern.fullmatch(value) is not None


def valid_splunk_index(value: Any) -> bool:
    """True for an SPL-safe index name; False for None / "" / anything else."""
    return _full(SPLUNK_INDEX_RE, value)


def valid_namespace(value: Any) -> bool:
    """True for a DNS-1123 label (an OpenShift namespace); False for None / ""."""
    return _full(DNS_LABEL_RE, value)


def valid_pod_name(value: Any) -> bool:
    """True for a DNS-1123 subdomain (a pod name); False for None / ""."""
    return _full(DNS_SUBDOMAIN_RE, value)
