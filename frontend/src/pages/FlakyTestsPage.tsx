/**
 * Flaky tests (`/flaky`, UX redesign P4): one page for the flaky-test job that
 * was split over Flaky Coach (`/flaky-coach`) and Quarantine (`/quarantine`),
 * both of which now redirect here.
 *
 * PLACEHOLDER while P4 is built: it renders Flaky Coach unchanged, so the new
 * route works from the first commit. P4 agent C replaces it with the tabbed
 * page (Detected · Proposed · Quarantined · History).
 *
 * The page needs one project, as Flaky Coach does: in All Projects mode it
 * says so here (the route-scope registry declares `/flaky` single-project, and
 * its ratchet reads this page for the prompt).
 */
import { HeartPulse } from 'lucide-react'
import PageHeader from '@/components/ui/PageHeader'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import FlakyCoachPage from './FlakyCoachPage'

export default function FlakyTestsPage() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  if (activeProjectId === ALL_PROJECTS_ID) {
    return (
      <div className="space-y-4">
        <PageHeader title="Flaky tests" subtitle="Select a specific project to see its flaky tests" />
        <ProjectRequiredEmptyState
          icon={<HeartPulse className="h-10 w-10" />}
          description="Flaky tests are analysed one project's history at a time."
        />
      </div>
    )
  }
  return <FlakyCoachPage />
}
