"""OpenShift / Kubernetes API client for pod metadata enrichment with retry.

W10 INT-01 / INT-03: the API URL and the service-account token come from the
runtime integration config passed as ``config`` (the settings saved at
``/settings/integrations``), falling back to THIS module's ``settings`` when
none is given, so a patch of ``app.services.ocp_client.settings`` still steers
it. Before any request: offline mode refuses, a namespace that is not a
DNS-1123 label or a pod name that is not a DNS-1123 subdomain is refused (both
are interpolated into the API path), and the host is checked with
``url_safety.egress_block_reason`` before the token is sent.
"""
import logging
from typing import Any, Optional, cast

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.integration_values import TARGET_NOT_ALLOWED, valid_namespace, valid_pod_name
from app.services.integration_config_service import IntegrationRuntimeConfig
from app.services.resilience import async_retry
from app.services.url_safety import egress_block_reason

logger = logging.getLogger(__name__)

OCP_INVALID_NAME_TEXT = "OpenShift lookup refused: the namespace or pod name is not valid."
OCP_NOT_ALLOWED_TEXT = f"OpenShift lookup refused: {TARGET_NOT_ALLOWED}."
#: The prefix of a summary that describes a pod (the server-side lookup keeps
#: only those).
OCP_FOUND_PREFIX = "Pod: "


async def get_pod_metadata(
    pod_name: str,
    namespace: str,
    *,
    config: Optional[IntegrationRuntimeConfig] = None,
    refusals: Optional[list[str]] = None,
) -> Optional[dict]:
    """Fetch pod spec, status, and events from the OpenShift API.

    ``None`` when the integration is off, offline mode is on, the names are
    not valid, the host is refused, or the API call fails. A refusal sentence
    (names, host) is appended to ``refusals`` when the caller passes a list,
    so ``analyze_pod_events`` can say why.
    """
    cfg = config or IntegrationRuntimeConfig.from_settings(settings)
    if not cfg.ocp_enabled or not cfg.ocp_api_url:
        return None
    # Re-audit N19: SECURITY.md s5 -- every outbound integration short-circuits
    # on AI_OFFLINE_MODE. The OCP probe was gated (H10 review); this client,
    # which the analysis path calls, sent the service-account token and pod
    # names to OCP_API_URL regardless. Hard gate, as the probe is.
    if settings.AI_OFFLINE_MODE:
        logger.info("OCP pod lookup skipped: AI_OFFLINE_MODE=true")
        return None
    if not valid_namespace(namespace) or not valid_pod_name(pod_name):
        logger.info("OCP pod lookup refused: the namespace or pod name is not a DNS-1123 name")
        if refusals is not None:
            refusals.append(OCP_INVALID_NAME_TEXT)
        return None
    block = await egress_block_reason(cfg.ocp_api_url)
    if block is not None:
        logger.info("OCP pod lookup refused before sending the token: %s", block)
        if refusals is not None:
            refusals.append(OCP_NOT_ALLOWED_TEXT)
        return None

    headers = {"Authorization": f"Bearer {cfg.ocp_sa_token}"} if cfg.ocp_sa_token else {}
    base = cfg.ocp_api_url.rstrip("/")

    async def _do_fetch() -> dict:
        client = get_http_client()
        # Fetch pod details
        pod_url = f"{base}/api/v1/namespaces/{namespace}/pods/{pod_name}"
        pod_resp = await client.get(pod_url, headers=headers, timeout=10.0)
        pod_resp.raise_for_status()
        pod_data = pod_resp.json()

        # Fetch pod events
        events_url = (
            f"{base}/api/v1/namespaces/{namespace}/events"
            f"?fieldSelector=involvedObject.name={pod_name}"
        )
        events_resp = await client.get(events_url, headers=headers, timeout=10.0)
        events_resp.raise_for_status()
        events_data = events_resp.json()

        container = pod_data.get("spec", {}).get("containers", [{}])[0]
        status = pod_data.get("status", {})

        return {
            "pod_name": pod_name,
            "namespace": namespace,
            "node": status.get("hostIP") or pod_data.get("spec", {}).get("nodeName"),
            "phase": status.get("phase"),
            "container_image": container.get("image"),
            "resources": container.get("resources", {}),
            "events": [
                {
                    "reason": e.get("reason"),
                    "message": e.get("message"),
                    "type": e.get("type"),
                    "first_time": e.get("firstTimestamp"),
                    "last_time": e.get("lastTimestamp"),
                    "count": e.get("count"),
                }
                for e in events_data.get("items", [])
            ],
        }

    try:
        return cast(Optional[dict[Any, Any]], await async_retry(
            _do_fetch,
            max_retries=2,
            base_delay=1.0,
            max_delay=10.0,
            operation_name=f"ocp_get_pod({pod_name}/{namespace})",
        ))
    except Exception as e:
        logger.warning("Failed to fetch OCP metadata for pod %s: %s", pod_name, type(e).__name__)
        return None


async def analyze_pod_events(
    pod_name: str,
    namespace: str,
    timestamp_utc: str,
    *,
    config: Optional[IntegrationRuntimeConfig] = None,
) -> str:
    """Analyse pod events for infrastructure failure indicators. Returns a human-readable summary."""
    refusals: list[str] = []
    meta = await get_pod_metadata(pod_name, namespace, config=config, refusals=refusals)
    if not meta:
        return refusals[0] if refusals else "OpenShift integration not configured or pod not found."

    events = meta.get("events", [])
    if not events:
        return f"No events found for pod '{pod_name}' in namespace '{namespace}'."

    critical_reasons = {"OOMKilled", "CrashLoopBackOff", "Evicted", "FailedScheduling", "BackOff"}
    critical_events = [e for e in events if e.get("reason") in critical_reasons]

    lines = [f"{OCP_FOUND_PREFIX}{pod_name} | Namespace: {namespace} | Phase: {meta.get('phase', 'Unknown')}"]
    if critical_events:
        lines.append(f"⚠️  {len(critical_events)} critical event(s) detected:")
        for e in critical_events:
            lines.append(f"  [{e['reason']}] {e['message']} (count: {e.get('count', 1)})")
    else:
        lines.append(f"No critical events. Total events: {len(events)}")
        for e in events[:3]:
            lines.append(f"  [{e.get('reason', '?')}] {e.get('message', '')}")

    resources = meta.get("resources", {})
    if resources:
        limits = resources.get("limits", {})
        lines.append(f"Resources — CPU: {limits.get('cpu', 'unlimited')} | Memory: {limits.get('memory', 'unlimited')}")

    return "\n".join(lines)
