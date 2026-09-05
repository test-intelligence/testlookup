/**
 * The "this page needs one project" prompt — with the control that resolves it.
 *
 * Six pages render a dead end while All Projects is active (see
 * `config/routeScope.ts`). Each previously wrote its own version, and every one
 * of them ended by telling the reader to go and use the top bar:
 *
 *   "Switch the project selector in the top bar to continue."
 *   "Pick a project from the top bar to manage its keys."
 *
 * That sentence is the bug. The page knows exactly what is missing, the remedy
 * is one control, and the user is sent to find it somewhere else — on a screen
 * that has just replaced everything they came for. Reported after clicking
 * "Open flaky coach" from the dashboard: the destination "shows a blank page".
 * It was not blank; it was this prompt, with nothing on it to press.
 *
 * The title stays exactly "Select a project" because
 * `GitLabIntegrationPage.test.tsx` and `RetentionPage.test.tsx` assert that
 * string, and those assertions are still the right ones — the prompt should
 * keep saying what it says. Nothing else here renders that exact text, so
 * `findByText` stays unambiguous.
 */
import type { ReactNode } from 'react'
import { FolderOpen } from 'lucide-react'
import EmptyState from './EmptyState'
import { useProjectStore } from '@/store/projectStore'

interface Props {
  /** Why THIS page needs one project. Keep it specific — a generic sentence
   *  gives the reader no way to tell a restriction from a fault. */
  description: string
  /** The page's own icon, so the prompt still looks like the page. */
  icon?: ReactNode
}

export default function ProjectRequiredEmptyState({ description, icon }: Props) {
  // Read-only against the shared store: no fetch of its own. Every route in
  // the app renders inside `AppLayout`, which mounts `TopBar`, which calls
  // `refreshProjects()` on mount — so a second request for the same list would
  // be redundant on every render of this prompt, including direct-URL landings.
  // An empty list here means the shared fetch has not resolved yet, and this
  // component re-renders when it does.
  const projects = useProjectStore(s => s.projects)
  const setActiveProject = useProjectStore(s => s.setActiveProject)

  return (
    <EmptyState
      icon={icon ?? <FolderOpen className="h-10 w-10" />}
      title="Select a project"
      description={description}
      action={
        <label className="flex flex-col items-center gap-1.5">
          <span className="text-[11px] uppercase tracking-wider text-[var(--color-text-muted)]">
            Choose one to continue
          </span>
          <select
            aria-label="Choose a project to continue"
            className="bg-[var(--color-bg-secondary)] border border-[var(--color-border)] text-[var(--color-text-secondary)] text-sm rounded-lg px-3 py-1.5 min-w-[220px] focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]"
            // Always the placeholder: reaching this prompt means no single
            // project is pinned, so there is no current value to show.
            value=""
            onChange={e => {
              const picked = projects.find(p => p.id === e.target.value)
              // Guard the lookup rather than passing `?? null`:
              // `setActiveProject(null)` maps back to ALL_PROJECTS_ID, which
              // would re-render this very prompt and read as a dead control.
              if (picked) setActiveProject(picked)
            }}
          >
            <option value="" disabled>
              Pick a project…
            </option>
            {/* True whether the shared fetch is still in flight or the
                deployment genuinely has none — this component cannot tell
                those apart, so it claims neither. */}
            {projects.length === 0 && (
              <option value="" disabled>
                No projects loaded
              </option>
            )}
            {projects.map(p => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      }
    />
  )
}
