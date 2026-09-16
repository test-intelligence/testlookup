import { getData, postData, putData } from './http'
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
    sample_limit: 20,
  })
}

export function publishWorkflow(
  projectId: string,
  workflowId: string,
  body: { accept_regression: boolean; reason?: string | null },
): Promise<WorkflowItem> {
  return postData(`${base(projectId)}/${workflowId}/publish`, body)
}
