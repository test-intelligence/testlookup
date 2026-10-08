/**
 * Release health on /releases (UX redesign P6, `02-design-spec.md` §2).
 *
 * The page had a verdict band AND a KPI strip side by side above the list,
 * so the list started 466 px down (the template's budget is 300). The
 * template allows ONE: here it is a `StatusBanner` —
 *
 *   pill   the gate decision of the release that ships next (the in-progress
 *          release with the nearest due date);
 *   title  the band's sentence about it ("2026.11 is blocked — was due …");
 *   facts  the composite pass rate when the gate has one, the open blockers
 *          across in-progress releases, and the counts Ready to ship,
 *          Blocked and Released · 30d (at most four: `releaseHealthFacts`);
 *   action opens that release in the side panel.
 *
 * Nothing the band and the strip said is gone: what does not fit the one
 * line is in the collapsed "Release health · this week" disclosure under the
 * list (`ReleaseHealthDetails`) — the composite against the 95 % gate, the
 * release's top three blockers, and the four count cards with their
 * sub-lines (`KpiStrip`) — and its summary keeps the In progress and
 * Released · 30d counts on screen while it is closed.
 *
 * Everything is counted client-side from the release list (`health.ts`):
 * no request of its own.
 */
import { AlertCircle, AlertTriangle, CheckCircle2 } from 'lucide-react'
import StatusBanner, { type BannerState } from '@/components/ui/StatusBanner'
import Disclosure from '@/components/ui/Disclosure'
import KpiStrip from './KpiStrip'
import { countReleases, NOTHING_IN_FLIGHT, releaseHeadline, releaseHealthFacts, releaseLede } from './health'
import type { DerivedBlocker, DerivedRelease, GateDecision } from './types'

/** The banner's state per gate decision; the pill keeps the gate badge's words where the state's own would misname it. */
const GATE_BANNER: Record<GateDecision, { state: BannerState; pill?: string }> = {
  go: { state: 'go' },
  conditional: { state: 'conditional', pill: 'CONDITIONAL GO' },
  no_go: { state: 'no_go' },
  not_evaluated: { state: 'pending', pill: 'NOT EVALUATED' },
  cancelled: { state: 'pending', pill: 'CANCELLED' },
}

const NO_RELEASE = { state: 'pending' as const, pill: 'NOTHING IN FLIGHT' }

interface HealthProps {
  /** The in-progress release with the nearest due date, if any. */
  highlighted: DerivedRelease | null
  inProgressReleases: DerivedRelease[]
  /** Every release on the page (not the filtered list): the counts are the project's. */
  releases: DerivedRelease[]
}

/** The one line above the list. `onOpen` (the list route) opens the release in the side panel. */
export function ReleaseHealthBanner({
  highlighted,
  inProgressReleases,
  releases,
  onOpen,
}: HealthProps & { onOpen?: (id: string) => void }) {
  const gate = highlighted ? GATE_BANNER[highlighted.gate.decision] : NO_RELEASE
  return (
    <section aria-label="Release health" data-release-health="">
      <StatusBanner
        state={gate.state}
        pillLabel={gate.pill}
        title={highlighted ? releaseHeadline(highlighted) : NOTHING_IN_FLIGHT}
        facts={releaseHealthFacts(highlighted, inProgressReleases, releases)}
        action={highlighted && onOpen ? { label: `Open ${highlighted.name}`, onClick: () => onOpen(highlighted.id) } : undefined}
      />
    </section>
  )
}

function BlockerIcon({ tone }: { tone: DerivedBlocker['severity'] }) {
  if (tone === 'resolved') return <CheckCircle2 aria-hidden="true" className="h-3 w-3 text-[var(--status-passed)]" />
  if (tone === 'warn') return <AlertTriangle aria-hidden="true" className="h-3 w-3 text-[var(--status-broken)]" />
  return <AlertCircle aria-hidden="true" className="h-3 w-3 text-[var(--status-failed)]" />
}

/** The collapsed disclosure under the list: the gate's detail, the top blockers, the four count cards. */
export function ReleaseHealthDetails({ highlighted, releases }: Omit<HealthProps, 'inProgressReleases'>) {
  const counts = countReleases(releases)
  return (
    <Disclosure
      title="Release health · this week"
      summary={`${counts.inProgress} in progress · ${counts.released30d} released in 30 days`}
      persistKey="releases.health"
    >
      <div className="space-y-3" data-release-health-details="">
        {highlighted && (
          <div>
            <p className="m-0 text-[13px] text-[var(--color-text-secondary)]">{releaseLede(highlighted)}</p>
            {highlighted.blockers.length > 0 && (
              <ul aria-label={`Top blockers on ${highlighted.name}`} className="mt-2.5 space-y-1.5 m-0 p-0 list-none">
                {highlighted.blockers.slice(0, 3).map((b) => (
                  <li key={b.id} className="flex items-center gap-2 text-[12.5px]">
                    <BlockerIcon tone={b.severity} />
                    <span className="text-[var(--color-text)]">{b.title}</span>
                    {b.context && <span className="text-[var(--color-text-muted)]">· {b.context}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <KpiStrip releases={releases} />
      </div>
    </Disclosure>
  )
}
