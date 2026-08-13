"""LangChain tool: analyze OpenShift pod events."""
from langchain_core.tools import tool

from app.services.ocp_client import analyze_pod_events
from app.tools.investigation_context import get_investigation_context
from app.services.evidence_sanitizer import sanitize_reference_text


@tool
async def analyze_openshift_pod_events(pod_name: str, namespace: str, timestamp_utc: str) -> str:
    """
    Query the OpenShift / Kubernetes API for pod events during the test execution window.
    Identifies infrastructure failures like OOMKilled, CrashLoopBackOff, Evicted,
    FailedScheduling, or resource pressure that would cause test failures unrelated to app code.

    Args:
        pod_name: The OpenShift pod name where the test ran (e.g. 'test-runner-abc123').
        namespace: The OpenShift namespace / Kubernetes namespace (e.g. 'qa-testing').
        timestamp_utc: ISO 8601 timestamp of the test failure.

    Returns:
        Pod status, resource limits, and any critical events as a formatted string.
    """
    context = get_investigation_context()
    if (
        context is None
        or not context.ocp_pod_name
        or not context.ocp_namespace
        or not context.timestamp
    ):
        return "OpenShift lookup unavailable: authorized pod context is missing."
    if (
        pod_name != context.ocp_pod_name
        or namespace != context.ocp_namespace
        or timestamp_utc != context.timestamp
    ):
        return "OpenShift lookup denied: requested scope differs from the investigation context."
    result = await analyze_pod_events(
        context.ocp_pod_name, context.ocp_namespace, context.timestamp
    )
    safe, _, _ = sanitize_reference_text(str(result), limit=6000)
    return safe
