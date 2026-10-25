import { deleteData, getData, postData, putData } from './http'
import type { WorkflowDefault, WorkflowDefaults } from '@/types/agentOps'
import type {
  WorkflowBody,
  WorkflowEvaluation,
  WorkflowItem,
  WorkflowValidation,
} from '@/types/workflowDefinition'

const base = (projectId: string) => `/api/v1/projects/${projectId}/workflows`

export async function listWorkflows(projectId: string): Promise<WorkflowItem[]> {
  const response = await getData<{ workflows: WorkflowItem[] }>(base(projectId))
  return response.workflows
}

export function updateWorkflow(
  projectId: string,
  workflowId: string,
  body: WorkflowBody,
): Promise<WorkflowItem> {
  return putData(`${base(projectId)}/${workflowId}`, body)
}

export function validateWorkflow(
  projectId: string,
  workflowId: string,
  version?: number,
): Promise<WorkflowValidation> {
  return postData(`${base(projectId)}/${workflowId}/validate`, undefined, {
    params: version ? { version } : undefined,
  })
}

export function forkWorkflow(
  projectId: string,
  workflowId: string,
  body: { workflow_id: string; name: string; description?: string | null },
  version?: number,
): Promise<WorkflowItem> {
  return postData(`${base(projectId)}/${workflowId}/fork`, body, {
    params: version ? { version } : undefined,
  })
}

export function evaluateWorkflow(
  projectId: string,
  workflowId: string,
  version: number,
): Promise<WorkflowEvaluation> {
  return postData(`${base(projectId)}/${workflowId}/evaluate`, {
    version,
    sample_limit: 100,
  })
}

export function publishWorkflow(
  projectId: string,
  workflowId: string,
  body: {
    version: number
    definition_sha256: string
    accept_regression: boolean
    reason?: string | null
    eval_manifest_checksum?: string | null
  },
): Promise<WorkflowItem> {
  return postData(`${base(projectId)}/${workflowId}/publish`, body)
}

// ── W14 AGT-08: the project's default workflow per base, and Delete draft ──
// Every path below is written out in full (the SPA wire-contract guard reads
// quoted literals), so none of them goes through the base() helper above.

export function getWorkflowDefaults(projectId: string): Promise<WorkflowDefaults> {
  return getData<WorkflowDefaults>(`/api/v1/projects/${projectId}/workflows/defaults`)
}

export function setWorkflowDefault(
  projectId: string,
  base: string,
  body: { workflow_id: string; workflow_version?: number | null },
): Promise<WorkflowDefault> {
  return putData<WorkflowDefault>(`/api/v1/projects/${projectId}/workflows/defaults/${base}`, body, {
    suppressToast: true,
  })
}

export function clearWorkflowDefault(projectId: string, base: string): Promise<void> {
  return deleteData<void>(`/api/v1/projects/${projectId}/workflows/defaults/${base}`, { suppressToast: true })
}

/** Delete the LATEST version of a fork when it is a draft (published versions are kept; a 409 otherwise). */
export function deleteWorkflow(projectId: string, workflowId: string): Promise<void> {
  return deleteData<void>(`/api/v1/projects/${projectId}/workflows/${encodeURIComponent(workflowId)}`, {
    suppressToast: true,
  })
}
