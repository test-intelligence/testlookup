/**
 * Helpers for the agent configuration panel (E4.3). Kept out of the component
 * file so React fast refresh sees a components-only module.
 */
import { isAxiosError } from 'axios'
import { extractErrorMessage } from '@/services/apiErrors'

/** `agent.root_cause_analysis.v1` → `Root cause analysis`. */
export function agentLabel(agentId: string): string {
  const stage = agentId.replace(/^agent\./, '').replace(/\.v\d+$/, '').replace(/_/g, ' ')
  return stage.charAt(0).toUpperCase() + stage.slice(1)
}

/** The server's reasons for refusing a save, one line each. */
export function saveErrorLines(err: unknown): string[] {
  if (!isAxiosError(err)) return ['Could not save the agent configuration.']
  const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item === 'string') return item
      const entry = item as { loc?: unknown[]; msg?: string }
      const where = (entry.loc ?? []).filter((part) => part !== 'body').join('.')
      return where ? `${where}: ${entry.msg ?? ''}` : String(entry.msg ?? '')
    })
  }
  return [extractErrorMessage(detail, err.message)]
}
