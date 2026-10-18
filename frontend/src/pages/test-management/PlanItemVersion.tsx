/**
 * A plan item's "Ran against" cell and the steps of the version it ran (W28
 * TCM-05). An execution records the case version current when it was
 * recorded (`executed_case_version`); editing the case later never changes it,
 * so the old execution keeps pointing at the steps it ran.
 *
 * The cell: "—" (not run), "not recorded" (run before versions were recorded),
 * a "v3" button, and "· case now v5" once the case moved on. The button opens
 * the steps under the row (the host renders them in an `InlineRowSection`);
 * they come from the case history (the existing SWR key, read only when
 * opened), which keeps the last 100 versions.
 */
import type { ReactElement } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTestCaseHistory } from '@/hooks/useTestManagement'
import type { TestCaseVersion, TestPlanItem } from '@/types/test-management'

function ranVersion(item: TestPlanItem): number | null {
  const v = item.executed_case_version
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function hasRun(item: TestPlanItem): boolean {
  const status = (item.execution_status ?? '').trim()
  return status !== '' && status !== 'not_run'
}

export function PlanItemVersionCell({ item, open, onToggle }: { item: TestPlanItem; open: boolean; onToggle: () => void }) {
  const ran = ranVersion(item)
  if (ran === null) {
    return (
      <span className="text-xs text-[var(--color-text-muted)] whitespace-nowrap" data-plan-item-version={hasRun(item) ? 'not-recorded' : 'not-run'}>
        {hasRun(item) ? 'not recorded' : '—'}
      </span>
    )
  }
  const now = typeof item.case_version === 'number' ? item.case_version : null
  return (
    <span className="text-xs whitespace-nowrap" data-plan-item-version={String(ran)}>
      <button
        type="button"
        className="font-mono text-[var(--color-accent)] hover:underline"
        aria-expanded={open}
        title={open ? `Hide the steps of v${ran}` : `Show the steps of v${ran}`}
        onClick={onToggle}
      >
        v{ran}
      </button>
      {now !== null && now !== ran && <span className="text-[var(--color-text-muted)]"> · case now v{now}</span>}
    </span>
  )
}

export function PlanItemVersionSteps({ item }: { item: TestPlanItem }) {
  const ran = ranVersion(item)
  const { data, error, isLoading } = useTestCaseHistory(ran === null ? undefined : item.test_case_id)
  if (ran === null) return <></>

  let body: ReactElement
  if (isLoading && !data) {
    body = <div className="flex justify-center py-3"><LoadingSpinner size="sm" /></div>
  } else if (error || !Array.isArray(data)) {
    body = <p className="m-0 text-xs text-[var(--color-text-muted)]">This case&apos;s history could not be read</p>
  } else {
    const version = (data as TestCaseVersion[]).find((v) => v.version === ran)
    if (!version) {
      body = <p className="m-0 text-xs text-[var(--color-text-muted)]">v{ran} is older than the last 100 versions</p>
    } else {
      const steps = Array.isArray(version.steps) ? version.steps : []
      body = (
        <div className="space-y-2">
          <p className="m-0 text-xs font-medium text-[var(--color-text)] [overflow-wrap:anywhere]">
            v{ran} · {version.title}
          </p>
          {steps.length === 0 ? (
            <p className="m-0 text-xs text-[var(--color-text-muted)]">No steps in v{ran}.</p>
          ) : (
            <ol className="m-0 pl-0 list-none space-y-1.5">
              {steps.map((step, index) => (
                <li key={index} className="text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]" data-plan-item-step="">
                  <span className="font-mono text-[var(--color-text-muted)]">#{index + 1}</span>{' '}
                  <span className="text-[var(--color-text)]">{step.action}</span>
                  {step.expected_result && (
                    <span className="block pl-5 text-[var(--color-text-muted)]">Expected: {step.expected_result}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
          {version.expected_result && (
            <p className="m-0 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]">
              <span className="font-medium">Expected result:</span> {version.expected_result}
            </p>
          )}
        </div>
      )
    }
  }

  return (
    <div className="space-y-1" data-plan-item-steps={String(ran)}>
      <p className="m-0 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
        Steps it ran against (v{ran})
      </p>
      {body}
    </div>
  )
}
