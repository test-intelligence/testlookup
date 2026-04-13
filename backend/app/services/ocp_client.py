"""OpenShift / Kubernetes API client for pod metadata enrichment with retry."""
import logging
from typing import Any, Optional, cast

from app.core.config import settings
from app.core.http_client import get_http_client
from app.services.resilience import async_retry

logger = logging.getLogger(__name__)


async def get_pod_metadata(pod_name: str, namespace: str) -> Optional[dict]:
    """Fetch pod spec, status, and events from the OpenShift API."""
    if not settings.OCP_ENABLED or not settings.OCP_API_URL:
        return None

    headers = {"Authorization": f"Bearer {settings.OCP_SA_TOKEN}"}
    base = settings.OCP_API_URL.rstrip("/")

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
        logger.warning("Failed to fetch OCP metadata for pod %s: %s", pod_name, e)
        return None


async def analyze_pod_events(pod_name: str, namespace: str, timestamp_utc: str) -> str:
    """Analyse pod events for infrastructure failure indicators. Returns a human-readable summary."""
    meta = await get_pod_metadata(pod_name, namespace)
    if not meta:
        return "OpenShift integration not configured or pod not found."

    events = meta.get("events", [])
    if not events:
        return f"No events found for pod '{pod_name}' in namespace '{namespace}'."

    critical_reasons = {"OOMKilled", "CrashLoopBackOff", "Evicted", "FailedScheduling", "BackOff"}
    critical_events = [e for e in events if e.get("reason") in critical_reasons]

    lines = [f"Pod: {pod_name} | Namespace: {namespace} | Phase: {meta.get('phase', 'Unknown')}"]
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
