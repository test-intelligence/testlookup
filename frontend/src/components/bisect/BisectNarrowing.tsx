/**
 * The bisect narrowing block (W23b FAIL-12): a failing test's own last pass
 * and first failure, the runs in between that did not run it, and — when a
 * git host answered — the commits between the two builds, ranked.
 *
 * Mounted where it is opened (a row's Suspects section on `/failures`, a
 * `Narrow` on the run page's Changes tab): mount = open, so the one read
 * (`GET /api/v1/runs/{run_id}/bisect?fingerprint=`, through
 * `useBisectNarrowing`) happens only then — never on page load. No polling;
 * a `timed_out` compare offers Retry. A failed or malformed read says
 * "Could not load the narrowed range." and leaves the rest of the section
 * alone. Every name, SHA and branch is React text; links go only to
 * `/runs/{uuid}` or a server-built http(s) URL.
 */
import { Link } from 'react-router-dom'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { INSIGHT_LINK_CLASS } from '@/components/insights/InsightsList'
import { useBisectNarrowing } from '@/hooks/useBisectNarrowing'
import type { BisectNarrowing as Narrowing, BisectRunRef } from '@/types/bisect'
import { safeCommitHref } from '@/pages/run/commitRangeReasons'
import BisectCommitList from './BisectCommitList'
import {
  BLOCK_HEADING, LOAD_ERROR_LINE, LOADING_LINE, NO_LOCATION_LINE, RETRY_LABEL, TRUNCATED_LINE,
  canRetry, compareLine, listedLine, mergeCaveat, metaParts, narrowedHeading, runHref, stateSegments,
  untestedLine, untestedMoreLine, type Segment,
} from './bisectModel'

const MUTED_XS = 'm-0 text-xs text-[var(--color-text-muted)]'
const BUILD_LINK = 'font-medium text-[var(--color-accent)] hover:underline'

export default function BisectNarrowing({
  runId,
  fingerprint,
  heading = BLOCK_HEADING,
  runBuild,
}: {
  runId: string
  fingerprint: string
  /** The `h3`: "Narrowed to this test's last pass" on `/failures`, "Narrowed range for {test}" on Changes. */
  heading?: string
  /** The run's own build number (for the `not_failing` sentence); the answer carries only its id. */
  runBuild?: string | null
}) {
  const { data, error, isLoading, retry } = useBisectNarrowing(runId, fingerprint)
  return (
    <section
      data-bisect-narrowing=""
      aria-label="Narrowed range"
      className="min-w-0 [overflow-wrap:anywhere]"
    >
      <h3 className="m-0 text-sm font-semibold text-[var(--color-text)]">{heading}</h3>
      {isLoading && !data ? (
        <div className="mt-1.5 flex items-center gap-2 text-sm text-[var(--color-text-muted)]" data-bisect-loading="">
          <LoadingSpinner size="sm" /> <span>{LOADING_LINE}</span>
        </div>
      ) : error || !data ? (
        <p className="m-0 mt-1.5 text-sm text-[var(--color-text-muted)]" data-bisect-error="">{LOAD_ERROR_LINE}</p>
      ) : (
        <NarrowingBody n={data} runBuild={runBuild} onRetry={retry} />
      )}
    </section>
  )
}

function SegmentText({ segment }: { segment: Segment }) {
  if (segment.kind === 'build') {
    return <Link to={runHref(segment.runId)} className={BUILD_LINK} data-bisect-build="">{segment.text}</Link>
  }
  if (segment.kind === 'sha') return <code className="font-mono text-sm" data-bisect-sha="">{segment.text}</code>
  return <>{segment.text}</>
}

function NarrowingBody({ n, runBuild, onRetry }: { n: Narrowing; runBuild?: string | null; onRetry: () => void }) {
  const meta = metaParts(n)
  const narrowed = n.state === 'narrowed'
  const ciGood = narrowed && safeCommitHref(n.good?.ci_run_url) !== null
  const ciBad = narrowed && safeCommitHref(n.bad?.ci_run_url) !== null
  const untested = untestedLine(n)
  const more = untestedMoreLine(n)
  const c = n.compare
  const reason = compareLine(n)
  return (
    <>
      <p className="m-0 mt-1.5 text-sm text-[var(--color-text)]" data-bisect-state={n.state}>
        {stateSegments(n, { runBuild }).map((s, i) => <SegmentText key={i} segment={s} />)}
      </p>
      {(meta.length > 0 || ciGood || ciBad) && (
        <p className={`${MUTED_XS} mt-1`} data-bisect-meta="">
          {meta.join(' · ')}
          {ciGood && <>{meta.length > 0 ? ' · ' : ''}<CiLink refRun={n.good} /></>}
          {ciBad && <>{meta.length > 0 || ciGood ? ' · ' : ''}<CiLink refRun={n.bad} /></>}
        </p>
      )}
      {untested && (
        <details className="mt-1.5" data-bisect-untested="">
          <summary className="cursor-pointer select-none text-xs text-[var(--color-text-muted)]">{untested}</summary>
          <ul className="m-0 mt-1 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-xs">
            {n.untested_runs.map((r) => (
              <li key={r.run_id}>
                <Link to={runHref(r.run_id)} className={BUILD_LINK}>#{r.build_number}</Link>
              </li>
            ))}
            {more && <li className="text-[var(--color-text-muted)]">{more}</li>}
          </ul>
        </details>
      )}
      {c.state === 'fetched' ? (
        <div className="mt-2.5 space-y-1.5" data-bisect-fetched="">
          <p className="m-0 text-sm font-medium text-[var(--color-text)]" data-bisect-narrowed-heading="">{narrowedHeading(n)}</p>
          {listedLine(n) && <p className={MUTED_XS}>{listedLine(n)}</p>}
          {c.truncated && <p className={MUTED_XS}>{TRUNCATED_LINE}</p>}
          {c.commits.length > 0 && !c.has_location_signal && <p className={MUTED_XS}>{NO_LOCATION_LINE}</p>}
          <BisectCommitList commits={c.commits} />
          {c.caveat && <p className={MUTED_XS} data-bisect-caveat="">{c.caveat}</p>}
          <p className={MUTED_XS} data-bisect-merge-caveat="">{mergeCaveat(n)}</p>
        </div>
      ) : reason ? (
        <p className={`${MUTED_XS} mt-2`} data-bisect-compare-reason={c.reason ?? ''}>
          {reason}
          {canRetry(n) && (
            <>
              {' '}
              <button type="button" onClick={() => onRetry()} className={INSIGHT_LINK_CLASS} data-bisect-retry="">
                {RETRY_LABEL}
              </button>
            </>
          )}
        </p>
      ) : null}
    </>
  )
}

function CiLink({ refRun }: { refRun: BisectRunRef | null | undefined }) {
  const href = safeCommitHref(refRun?.ci_run_url)
  if (!refRun || !href) return null
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" data-bisect-ci-link="" className="text-[var(--color-accent)] hover:underline">
      CI build #{refRun.build_number} ↗
    </a>
  )
}
