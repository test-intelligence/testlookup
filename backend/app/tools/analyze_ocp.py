"""LangChain tool: analyze OpenShift pod events.

W10 INT-01 / INT-03: the OpenShift API URL and token come from the bound
runtime integration config (``investigation_context.runtime_integrations()``);
``ocp_client`` falls back to its own ``settings`` when nothing is bound. A
lookup the server already ran for this investigation (D-4) answers from the
memo instead of a second call.
"""
from langchain_core.tools import tool

from app.core.config import settings
from app.services.ocp_client import analyze_pod_events
from app.tools.investigation_context import (
    get_investigation_context,
    prefetched_observation,
    runtime_integrations,
)
from app.services.evidence_sanitizer import sanitize_reference_text

TOOL_NAME = "analyze_openshift_pod_events"


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
    if not settings.AI_OFFLINE_MODE:
        memo = prefetched_observation(TOOL_NAME)
        if memo is not None:
            return memo
    result = await analyze_pod_events(
        context.ocp_pod_name, context.ocp_namespace, context.timestamp,
        config=runtime_integrations(),
    )
    safe, _, _ = sanitize_reference_text(str(result), limit=6000)
    return safe
