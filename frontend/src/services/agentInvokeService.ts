import { getData, postData } from './http'
import type { AgentCatalogDetail, AgentCatalogEntry, AgentInvokeBody } from '@/types/agentInvoke'

/**
 * W23c AGT-11: the agent catalog and one-agent invocations (`/agents` › Try an agent).
 *
 * - catalog / catalog detail: any signed-in user — reads only;
 * - invoke: QA engineer and above; the anchor run (`input.payload.test_run_id`) must be a stored run of
 *   `project_id`. Every invoke sends an `Idempotency-Key` (one per Run click: a retried request with the
 *   same key never starts a second invocation) and passes `suppressToast: true` — the panel renders the
 *   413 / 422 / 404 `detail.message` (and a 422's `errors`) inline under the editor;
 * - invocation: project members (404 otherwise); the answer is RAW, read through `parseInvocation` by
 *   `hooks/useAgentInvoke`.
 *
 * Every request path is written out in full in its call (the SPA wire-contract guard reads them).
 */

/** A fresh Idempotency-Key (mint ONE per Run click and pass it to `invoke`). */
export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

export function listCatalog(): Promise<AgentCatalogEntry[]> {
  return getData<AgentCatalogEntry[]>('/api/v1/agents/catalog')
}

export function getCatalogAgent(agentId: string): Promise<AgentCatalogDetail> {
  return getData<AgentCatalogDetail>(`/api/v1/agents/catalog/${encodeURIComponent(agentId)}`)
}

export function invoke(
  agentId: string,
  body: AgentInvokeBody,
  idempotencyKey: string = newIdempotencyKey(),
): Promise<unknown> {
  return postData<unknown, AgentInvokeBody>(`/api/v1/agents/${encodeURIComponent(agentId)}/invoke`, body, {
    headers: { 'Idempotency-Key': idempotencyKey },
    suppressToast: true,
  })
}

export function getInvocation(invocationId: string): Promise<unknown> {
  return getData<unknown>(`/api/v1/agents/invocations/${encodeURIComponent(invocationId)}`)
}
