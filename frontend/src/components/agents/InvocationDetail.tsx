/**
 * One agent invocation, read back (W23c AGT-11): shown by `/agents` › Try an agent once a Run is accepted, and
 * on load for `?invocation=<id>` (the Approvals row links here). A SIBLING of `PipelineDetail`, not a change to it.
 *
 * `useInvocation` polls with a NUMBER — `REFRESH_INTERVALS.REALTIME` while the invocation is in progress, 0
 * after — so it stops once the agent finished. The header names the agent, the anchor run (a link to it), the
 * status and the input kind; a draft awaiting review says so and links to the review queue; an inline-input
 * output says it is kept out of every rollup. The output and the stored input are React text inside `<pre>`
 * (a `<script>` in either shows literally), and the stored input is the SERVER's redacted copy — never what was
 * typed in the editor.
 */
import { Link } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useInvocation } from '@/hooks/useAgentInvoke'
import { REPORTS_QUEUE_HREF } from '@/pages/inbox/agentActionModel'
import {
  INLINE_SENTENCES,
  INVOCATION_LOAD_ERROR,
  NO_OUTPUT,
  NO_OUTPUT_YET,
  OUTPUT_TITLE,
  QUEUE_LINK,
  executiveSummary,
  inputLine,
  reviewLine,
  runFallbackLabel,
  statusWord,
} from './tryAgentModel'

const TERM = 'text-[var(--color-text-muted)]'
const PRE =
  'm-0 max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-[var(--color-border)] ' +
  'bg-[var(--color-bg-secondary)] p-3 font-mono text-xs text-[var(--color-text)] [overflow-wrap:anywhere]'
const SUBHEAD = 'm-0 text-sm font-semibold text-[var(--color-text)]'

export interface InvocationDetailProps {
  invocationId: string
  /** The run picked on the page and how its picker names it: used only when it IS the invocation's run. */
  anchor?: { runId: string; label: string | null } | null
}

export default function InvocationDetail({ invocationId, anchor }: InvocationDetailProps) {
  const { data: inv, error, mutate } = useInvocation(invocationId)

  if (error && !inv) {
    return (
      <div role="alert" data-invocation-detail="" data-invocation-error="" className="flex flex-col items-start gap-2 border-t border-[var(--color-border)] pt-3">
        <p className="m-0 text-sm font-medium text-[var(--color-text)]">{INVOCATION_LOAD_ERROR}</p>
        <button type="button" className="btn-secondary btn-sm" onClick={() => void mutate()}>Retry</button>
      </div>
    )
  }
  if (!inv) {
    return (
      <div data-invocation-detail="" className="border-t border-[var(--color-border)] pt-3">
        <LoadingSpinner size="sm" />
      </div>
    )
  }

  const inline = inv.input_kind === 'inline'
  const review = reviewLine(inv)
  const awaiting = inv.requires_human_review && inv.review?.state === 'pending_review'
  const summary = executiveSummary(inv.output)
  const run = (anchor?.runId === inv.test_run_id && anchor.label) || runFallbackLabel(inv.test_run_id)

  return (
    // Flex gaps, not space-y: Tailwind v4's space-y margin sits in a zero-specificity :where() that the children's
    // own m-0 overrides, so these blocks touched (W23c baseline read).
    <div data-invocation-detail="" data-invocation-status={inv.status} className="flex flex-col gap-3 border-t border-[var(--color-border)] pt-3">
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className={TERM}>Agent</dt>
        <dd className="m-0 min-w-0 truncate font-mono text-[var(--color-text)]" title={inv.agent_id}>{inv.agent_id}</dd>
        <dt className={TERM}>Run</dt>
        <dd className="m-0 min-w-0 truncate">
          <Link to={`/runs/${encodeURIComponent(inv.test_run_id)}`} title={run} className="text-[var(--color-accent)] hover:underline">
            {run}
          </Link>
        </dd>
        <dt className={TERM}>Status</dt>
        <dd className="m-0 inline-flex items-center gap-2 text-[var(--color-text)]" data-invocation-status-word="">
          {statusWord(inv)}
          {inv.status === 'in_progress' && <LoadingSpinner size="sm" />}
        </dd>
        <dt className={TERM}>Input</dt>
        <dd className="m-0 text-[var(--color-text)]">{inputLine(inv)}</dd>
      </dl>

      {(review || inline || inv.error) && (
        <div className="flex flex-col gap-1">
          {review && (
            <p className="m-0 text-sm text-[var(--color-text)]" data-invocation-review="">
              {review}
              {awaiting && (
                <>
                  {' '}
                  <Link to={REPORTS_QUEUE_HREF} className="whitespace-nowrap text-[var(--color-accent)] hover:underline">{QUEUE_LINK}</Link>
                </>
              )}
            </p>
          )}
          {inline && <p className="m-0 text-xs text-[var(--color-text-muted)]" data-invocation-excluded="">{INLINE_SENTENCES.excluded}</p>}
          {inv.error && <p className="m-0 text-xs text-[var(--color-text-secondary)] [overflow-wrap:anywhere]" data-invocation-error-text="">{inv.error}</p>}
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <h3 className={SUBHEAD}>{OUTPUT_TITLE}</h3>
        {summary && <p className="m-0 text-sm text-[var(--color-text)] [overflow-wrap:anywhere]">{summary}</p>}
        {inv.output ? (
          <pre className={PRE} data-invocation-output="">{JSON.stringify(inv.output, null, 2)}</pre>
        ) : (
          <p className="m-0 text-xs text-[var(--color-text-muted)]">{inv.status === 'in_progress' ? NO_OUTPUT_YET : NO_OUTPUT}</p>
        )}
      </div>

      {inline && inv.input && (
        <div className="flex flex-col gap-1.5">
          <h3 className={SUBHEAD}>{INLINE_SENTENCES.stored_input_redacted}</h3>
          <pre className={PRE} data-invocation-stored-input="">{JSON.stringify(inv.input, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}
