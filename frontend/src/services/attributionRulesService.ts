import { deleteData, getData, postData, putData } from './http'
import type {
  AttributionPreviewResponse,
  AttributionRule,
  AttributionRuleFieldsResponse,
  AttributionRuleInput,
  AttributionRuleList,
  AttributionRuleUpdate,
} from '@/types/attributionRules'

/**
 * Release attribution rules (W11 REL-09): `/api/v1/projects/{project_id}/attribution-rules`.
 *
 * Reads go through `hooks/useAttributionRules` (SWR). The writes and the
 * preview are the page's: their failures render in the form, so they skip the
 * global toast (`suppressToast`) and the caller shows the server's sentence.
 */
const base = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/attribution-rules`

export const attributionRulesService = {
  list: (projectId: string) => getData<AttributionRuleList>(base(projectId)),

  fields: (projectId: string) => getData<AttributionRuleFieldsResponse>(`${base(projectId)}/fields`),

  create: (projectId: string, body: AttributionRuleInput) =>
    postData<AttributionRule, AttributionRuleInput>(base(projectId), body, { suppressToast: true }),

  update: (projectId: string, ruleId: string, body: AttributionRuleUpdate) =>
    putData<AttributionRule, AttributionRuleUpdate>(`${base(projectId)}/${encodeURIComponent(ruleId)}`, body, {
      suppressToast: true,
    }),

  remove: (projectId: string, ruleId: string) =>
    deleteData(`${base(projectId)}/${encodeURIComponent(ruleId)}`, { suppressToast: true }),

  /** Try `body` against the last 200 runs, saving nothing. `ruleId`: the rule being edited. */
  preview: (projectId: string, body: AttributionRuleInput, ruleId?: string | null) =>
    postData<AttributionPreviewResponse, AttributionRuleInput>(`${base(projectId)}/preview`, body, {
      params: ruleId ? { detail: true, rule_id: ruleId } : { detail: true },
      suppressToast: true,
    }),
}
