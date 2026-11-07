/**
 * STRAT-03 — the epic action on one systemic flake cluster (Failures › Groups ›
 * Systemic flake clusters), handed to `SystemicClusters` as its
 * `renderClusterAction` by FailureGroupsSection.
 *
 * Suggest-only: nothing here writes to Jira. A person opens the form and
 * clicks Create; the opt-in is per project and off by default.
 *
 * One status request per project (`GET .../systemic-clusters/epics`, the SWR
 * key `clusterEpicsKey(projectId)`), read by every cluster row. Per cluster,
 * exactly one of:
 *   nothing     the project has not opted in, the status has not arrived (or
 *               failed), the cluster has no membership key, it does not recur
 *               (`co_failure_runs` below `min_co_failure_runs`), or the reader
 *               is below QA engineer (the role the POST requires)
 *   linked      a stored link for this membership key: "Epic <KEY>", opening
 *               Jira in a new tab (any role may follow it)
 *   disabled    opted in and recurring, but Jira is unavailable or has no Epic
 *               issue type: the button disabled, the reason as visible text
 *   create      "Create epic for this cluster" opens CreateClusterEpicForm
 *               under the row
 *
 * A link is keyed by the cluster's membership key, the hash of its exact
 * member set: when a test joins or leaves, the cluster is a new one with no
 * link (the clusters footer says what the key promises).
 */
import { useState } from 'react'
import type { SystemicCluster } from '@/components/charts/failureGroups/systemicClusters.model'
import { useClusterEpics } from '@/hooks/useJiraDefects'
import { usePermissions } from '@/hooks/usePermissions'
import type { ClusterEpicsStatus } from '@/services/defectJiraService'
import CreateClusterEpicForm from './CreateClusterEpicForm'

export const CREATE_EPIC_LABEL = 'Create epic for this cluster'
/** Shown when Jira is unavailable and the server gave no sentence of its own. */
export const JIRA_UNAVAILABLE_FALLBACK = 'Jira is unavailable.'
export const NO_EPIC_TYPE_REASON = 'The Jira project has no Epic issue type, so an epic cannot be filed from here.'

type View =
  | { kind: 'none' }
  | { kind: 'linked'; jiraKey: string; jiraUrl: string | null }
  | { kind: 'disabled'; reason: string }
  | { kind: 'create' }

const NONE: View = { kind: 'none' }

/** What one cluster row shows, from the project's status, the cluster and the reader's role. */
function viewFor(status: ClusterEpicsStatus | undefined, cluster: SystemicCluster, canCreate: boolean): View {
  if (!status || !status.enabled || !cluster.membershipKey) return NONE
  const link = status.links.find((candidate) => candidate.membership_key === cluster.membershipKey)
  if (link) return { kind: 'linked', jiraKey: link.jira_key, jiraUrl: link.jira_url }
  if (!canCreate) return NONE
  if (cluster.coFailureRuns === null || cluster.coFailureRuns < status.min_co_failure_runs) return NONE
  if (!status.available) return { kind: 'disabled', reason: status.reason || JIRA_UNAVAILABLE_FALLBACK }
  if (status.epic_type_available === false) return { kind: 'disabled', reason: NO_EPIC_TYPE_REASON }
  return { kind: 'create' }
}

const ACTION =
  'inline-flex items-center rounded border border-[var(--color-border)] px-2 py-0.5 text-xs text-[var(--color-text)] ' +
  'hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50'

export interface ClusterEpicActionProps {
  projectId: string
  cluster: SystemicCluster
}

export default function ClusterEpicAction({ projectId, cluster }: ClusterEpicActionProps) {
  const { status, mutate } = useClusterEpics(projectId)
  const { isQaEngineer } = usePermissions()
  const [open, setOpen] = useState(false)
  const view = viewFor(status, cluster, isQaEngineer)

  // An open form stays until it is closed, even once the revalidated status links the cluster: it holds the
  // filed link (or the outcome-unknown question) the person is reading.
  if (open && cluster.membershipKey) {
    return (
      <div data-cluster-epic="form" className="mt-2">
        <CreateClusterEpicForm
          projectId={projectId}
          membershipKey={cluster.membershipKey}
          clusterLabel={cluster.label}
          onClose={() => setOpen(false)}
          onCreated={() => void mutate()}
        />
      </div>
    )
  }

  if (view.kind === 'none') return null
  if (view.kind === 'linked') {
    return (
      <p data-cluster-epic="linked" className="m-0 mt-1 text-xs">
        {view.jiraUrl ? (
          <a href={view.jiraUrl} target="_blank" rel="noreferrer" className="font-medium text-[var(--color-accent)] underline">
            Epic {view.jiraKey}
          </a>
        ) : (
          <span className="font-medium">Epic {view.jiraKey}</span>
        )}
      </p>
    )
  }
  if (view.kind === 'disabled') {
    const reasonId = `cluster-epic-reason-${cluster.key}`
    return (
      <p data-cluster-epic="disabled" className="m-0 mt-1 flex flex-wrap items-center gap-2 text-xs">
        <button type="button" disabled aria-describedby={reasonId} className={ACTION}>
          {CREATE_EPIC_LABEL}
        </button>
        <span id={reasonId} data-cluster-epic-reason="" className="text-[var(--color-text-secondary)]">
          {view.reason}
        </span>
      </p>
    )
  }
  return (
    <p data-cluster-epic="create" className="m-0 mt-1 text-xs">
      <button type="button" onClick={() => setOpen(true)} className={ACTION}>
        {CREATE_EPIC_LABEL}
      </button>
    </p>
  )
}
