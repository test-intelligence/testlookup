/**
 * Inbox › Approvals (UX redesign P4 item 5, owner decision D4): one place for
 * everything waiting on a person's decision in the active project —
 *
 *   - AI reports: the review queue that was `/reviews` (`ReviewsBody`);
 *   - Quarantine proposals: the requests Flaky tests › Proposed shows
 *     (`QuarantineBody`, Approve / Reject);
 *   - Test cases: the cases awaiting review (`CaseApprovals`);
 *   - Defects (W06 DEF-06, QA leads only): promoted defects awaiting approval
 *     (`DefectApprovals`). The list is QA_LEAD-only on the server, so for
 *     anyone else the chip and the section are absent and the queue is never
 *     requested (no 403 toast on every Inbox visit).
 *
 * A source filter (`?source=`) narrows it to one of them; "All" stacks them,
 * each under its heading with its count. Every queue belongs to one
 * project, so in All Projects mode the tab shows the project prompt `/reviews`
 * showed and asks for nothing.
 */
import type { ReactNode } from 'react'
import { ClipboardCheck } from 'lucide-react'
import ProjectRequiredEmptyState from '@/components/ui/ProjectRequiredEmptyState'
import { TabCount } from '@/components/ui/Tabs'
import { useTabParam } from '@/components/ui/useTabParam'
import ScopedLink from '@/components/ui/ScopedLink'
import { usePermissions } from '@/hooks/usePermissions'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { ReviewsBody } from '../ReviewsPage'
import { QuarantineBody } from '../QuarantinePage'
import CaseApprovals from './CaseApprovals'
import ChipFilter, { type ChipOption } from './ChipFilter'
import DefectApprovals from './DefectApprovals'
import { useApprovalCounts } from './approvalQueues'

const APPROVAL_SOURCES = ['all', 'reports', 'quarantine', 'cases', 'defects'] as const
type ApprovalSource = (typeof APPROVAL_SOURCES)[number]
/** What anyone but a QA lead may pick: `?source=defects` then reads as "All". */
const NON_LEAD_SOURCES: readonly ApprovalSource[] = APPROVAL_SOURCES.filter((s) => s !== 'defects')

export default function ApprovalsTab() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  if (activeProjectId === ALL_PROJECTS_ID || !activeProjectId) {
    return (
      <ProjectRequiredEmptyState
        icon={<ClipboardCheck className="h-10 w-10" />}
        description="Approvals belong to one project. Pick the project whose AI reports, quarantine proposals, test-case reviews and defect promotions you want to decide."
      />
    )
  }
  return <Approvals projectId={activeProjectId} />
}

function Approvals({ projectId }: { projectId: string }) {
  const { isQaLead } = usePermissions()
  const [source, setSource] = useTabParam(isQaLead ? APPROVAL_SOURCES : NON_LEAD_SOURCES, 'all', 'source')
  const counts = useApprovalCounts(projectId)
  const show = (which: Exclude<ApprovalSource, 'all'>) => source === 'all' || source === which
  const options: ChipOption<ApprovalSource>[] = [
    { id: 'all', label: 'All', count: counts.total },
    { id: 'reports', label: 'AI reports', count: counts.reports },
    { id: 'quarantine', label: 'Quarantine proposals', count: counts.quarantine },
    { id: 'cases', label: 'Test cases', count: counts.cases },
    ...(isQaLead ? [{ id: 'defects' as const, label: 'Defects', count: counts.defects }] : []),
  ]

  return (
    <div className="space-y-4">
      <ChipFilter
        label="Source"
        ariaLabel="Approval source"
        value={source}
        onChange={setSource}
        options={options}
      />

      <div data-primary="" className="space-y-6">
        {show('reports') && (
          <ApprovalSection id="reports" title="AI reports" count={counts.reports} heading={source === 'all'}>
            <ReviewsBody />
          </ApprovalSection>
        )}
        {show('quarantine') && (
          <ApprovalSection
            id="quarantine"
            title="Quarantine proposals"
            count={counts.quarantine}
            heading={source === 'all'}
            more={
              <ScopedLink to="/flaky?tab=proposed" className="text-xs text-[var(--color-accent)] hover:underline">
                Open in Flaky tests
              </ScopedLink>
            }
          >
            <QuarantineBody view="proposals" projectId={projectId} />
          </ApprovalSection>
        )}
        {show('cases') && (
          <ApprovalSection id="cases" title="Test cases" count={counts.cases} heading={source === 'all'}>
            <CaseApprovals />
          </ApprovalSection>
        )}
        {isQaLead && show('defects') && (
          <ApprovalSection
            id="defects"
            title="Defects"
            count={counts.defects}
            heading={source === 'all'}
            more={
              <ScopedLink to="/defects" className="text-xs text-[var(--color-accent)] hover:underline">
                Open Defects
              </ScopedLink>
            }
          >
            <DefectApprovals projectId={projectId} />
          </ApprovalSection>
        )}
      </div>
    </div>
  )
}

/** One source's queue; under "All" it carries a heading with its count. */
function ApprovalSection({
  id,
  title,
  count,
  heading,
  more,
  children,
}: {
  id: Exclude<ApprovalSource, 'all'>
  title: string
  count: number | undefined
  heading: boolean
  more?: ReactNode
  children: ReactNode
}) {
  return (
    <section aria-label={title} data-approval-source={id} className="space-y-2">
      {(heading || more) && (
        <div className="flex items-center gap-2">
          {heading && (
            <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
              {title}
              {count !== undefined && <TabCount value={count} active={false} />}
            </h2>
          )}
          {more && <span className="ml-auto">{more}</span>}
        </div>
      )}
      {children}
    </section>
  )
}

/** The Approvals tab label's count (everything waiting); mounted only while one project is active. */
export function ApprovalsCountBadge({ projectId, active }: { projectId: string; active: boolean }) {
  const { total } = useApprovalCounts(projectId)
  return total === undefined ? null : <TabCount value={total} active={active} />
}
