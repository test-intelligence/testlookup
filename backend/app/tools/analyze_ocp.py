"""LangChain tool: analyze OpenShift pod events."""
from langchain_core.tools import tool

from app.services.ocp_client import analyze_pod_events


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
    return await analyze_pod_events(pod_name, namespace, timestamp_utc)
