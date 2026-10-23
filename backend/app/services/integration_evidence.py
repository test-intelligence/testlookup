"""Server-side integration lookups that run before the triage model call (W10 D-4).

Small models skip tools (mistral-nemo, the homelab's analysis class), and a
stack without a reachable model never gets to call one. So the evidence the
investigation context already authorizes -- the Splunk correlation for the
failing service at the failure time, the OpenShift events of the pod the run
named -- is fetched by the SERVER, from the bound context and the resolved
runtime integration config, before ``executor.ainvoke``:

* the result is attached to the model's question (the model is told not to
  call those tools again, and the tools answer from a memo if it does);
* each lookup that found something is recorded as a server-side tool step,
  kept on every exit of the agent run (an LLM answer, the rules fallback, the
  canned stub), so ``tools_used`` and the evidence references show it.

No database session and no model call here; tokens never leave the config.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Awaitable, Optional

from app.core.config import settings
from app.services.workflow_step_context import tool_allowed

if TYPE_CHECKING:
    from langchain_core.agents import AgentAction

    from app.services.integration_config_service import IntegrationRuntimeConfig
    from app.tools.investigation_context import InvestigationContext

logger = logging.getLogger(__name__)

SERVER_LOOKUP_LOG = "server-side lookup"
SERVER_LOOKUP_TOOLS = ("query_splunk_logs", "analyze_openshift_pod_events")


async def _splunk_lookup(
    context: "InvestigationContext", cfg: "IntegrationRuntimeConfig",
) -> Optional[tuple[str, dict, str]]:
    from app.services.input_sanitizer import sanitize_query_param, sanitize_service_name
    from app.tools import query_splunk

    service = sanitize_service_name(context.service_name or "")
    timestamp = sanitize_query_param(context.timestamp or "")
    if not service or not timestamp:
        return None
    text = await query_splunk.splunk_correlation(cfg, service, timestamp)
    if not text.startswith(query_splunk.SPLUNK_FOUND_PREFIX):
        return None
    args = {"service_name": context.service_name, "timestamp_utc": context.timestamp}
    return SERVER_LOOKUP_TOOLS[0], args, text


async def _ocp_lookup(
    context: "InvestigationContext", cfg: "IntegrationRuntimeConfig",
) -> Optional[tuple[str, dict, str]]:
    from app.services import ocp_client
    from app.services.evidence_sanitizer import sanitize_reference_text

    result = await ocp_client.analyze_pod_events(
        str(context.ocp_pod_name), str(context.ocp_namespace), str(context.timestamp), config=cfg,
    )
    # What the tool itself returns (tools/analyze_ocp.py), so the memo and a
    # tool call read the same text.
    text, _, _ = sanitize_reference_text(str(result), limit=6000)
    if not text.startswith(ocp_client.OCP_FOUND_PREFIX):
        return None
    args = {
        "pod_name": context.ocp_pod_name,
        "namespace": context.ocp_namespace,
        "timestamp_utc": context.timestamp,
    }
    return SERVER_LOOKUP_TOOLS[1], args, text


async def gather_server_side_lookups(
    context: "InvestigationContext | None",
    cfg: "IntegrationRuntimeConfig | None",
) -> list[tuple["AgentAction", str]]:
    """Run the authorized lookups concurrently; keep the ones that found something.

    Splunk when the context names a service and a timestamp, the step's tool
    grant allows ``query_splunk_logs`` and ``cfg.splunk_unavailable_reason()``
    is None; OpenShift when it names a pod, a namespace and a timestamp, the
    grant allows ``analyze_openshift_pod_events`` and
    ``cfg.ocp_unavailable_reason()`` is None. ``cfg`` None (the resolve
    failed) uses the environment, as the tools do. Each kept step is
    ``(AgentAction(tool, json.dumps(<the context's args>), SERVER_LOOKUP_LOG),
    <the observation the model would have seen>)``. Never raises.
    """
    if context is None:
        return []
    try:
        from langchain_core.agents import AgentAction

        from app.services.input_sanitizer import sanitize_tool_output
        from app.services.integration_config_service import IntegrationRuntimeConfig

        effective = cfg or IntegrationRuntimeConfig.from_settings(settings, context.project_id)
        lookups: list[Awaitable[Optional[tuple[str, dict, str]]]] = []
        if (
            context.service_name and context.timestamp
            and tool_allowed(SERVER_LOOKUP_TOOLS[0])
            and effective.splunk_unavailable_reason() is None
        ):
            lookups.append(_splunk_lookup(context, effective))
        if (
            context.ocp_pod_name and context.ocp_namespace and context.timestamp
            and tool_allowed(SERVER_LOOKUP_TOOLS[1])
            and effective.ocp_unavailable_reason() is None
        ):
            lookups.append(_ocp_lookup(context, effective))
        if not lookups:
            return []

        outcomes = await asyncio.gather(*lookups, return_exceptions=True)
        steps: list[tuple[AgentAction, str]] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.debug("server-side lookup failed: %s", type(outcome).__name__)
                continue
            if outcome is None:
                continue
            tool_name, args, text = outcome
            # The sanitizer the ReAct executor applies to every tool output
            # (services/agent.py _sanitizing_copy): the text the model sees.
            observation = sanitize_tool_output(text)
            steps.append((
                AgentAction(tool=tool_name, tool_input=json.dumps(args), log=SERVER_LOOKUP_LOG),
                observation,
            ))
        return steps
    except Exception as exc:  # noqa: BLE001 -- evidence is best-effort, never the end of the analysis
        logger.debug("server-side lookups skipped: %s", type(exc).__name__)
        return []
