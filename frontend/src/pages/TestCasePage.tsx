/**
 * One test's result in one run (`/runs/:runId/tests/:testId`) — UX redesign
 * P4, `02-design-spec.md` §5 "Test case": the trail back to its run, the
 * header (the test's name, its full name), then the body — chips, the error +
 * stack trace + AI root cause in the first viewport, and the History · Steps ·
 * Details tabs (`testCase/TestCaseBody`, which the Run page also opens in a
 * side panel).
 *
 * Deleted per §5: the metadata grid that repeated the chips (its severity,
 * feature, owner and run date are in Details), and the hard-coded
 * `<Class>.java` title on the trace.
 */
import { useLocation, useParams } from 'react-router-dom'
import PageShell from '@/components/layout/PageShell'
import Breadcrumbs from '@/components/ui/Breadcrumbs'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import PageHeader from '@/components/ui/PageHeader'
import { helpTopicParam } from '@/components/help/helpTopics'
import { useTestCase } from '@/hooks/useRuns'
import { useProjectChangeRedirect } from '@/hooks/useProjectChange'
import TestCaseBody from './testCase/TestCaseBody'

export default function TestCasePage() {
  const { runId, testId } = useParams<{ runId: string; testId: string }>()
  const { pathname } = useLocation()
  useProjectChangeRedirect('/runs', Boolean(runId || testId))
  // The body reads the same SWR key: one request for both.
  const { data: tc, isLoading } = useTestCase(runId, testId)

  if (isLoading) return <div className="flex items-center justify-center h-64"><LoadingSpinner size="lg" /></div>
  if (!tc || !runId || !testId) return <div className="text-[var(--color-text-muted)] text-center py-20">Test case not found</div>

  return (
    <PageShell className="space-y-4">
      <Breadcrumbs
        items={[
          { label: 'Runs', to: '/runs' },
          { label: `#${runId.slice(0, 8)}`, to: `/runs/${runId}` },
          { label: tc.test_name },
        ]}
      />
      <PageHeader
        compact
        title={tc.test_name}
        subtitle={tc.full_name ?? tc.class_name}
        helpTopic={helpTopicParam(pathname)}
      />
      <TestCaseBody runId={runId} testId={testId} />
    </PageShell>
  )
}
