/**
 * W08 TRND-08 — Value metrics › By team (`?view=team`): one row per owning
 * team (the project's ownership rules, resolved by the server's shared
 * resolver) plus "Unassigned", last; then the two totals that make the table
 * add up to the page's headline:
 *
 *   Not attributable to a team: {h} h (runs unblocked by quarantine are counted per run)
 *   Total {headline} h — the headline for these {N} days
 *
 * "Not measured" is never 0: a team with nothing evaluated has no pass rate
 * ("—"), a team with no resolved defect no MTTR ("—").
 *
 * A team name is user text: React text, and in a link only as the
 * `encodeURIComponent`ed `?team=` value. The link is drawn only for QA lead
 * and above (`/ownership` is theirs); Unassigned never links.
 */
// ScopedLink, not Link: /ownership cannot render in All Projects, and the
// route-scope ratchet holds every link to a project-gated page to it.
import ScopedLink from '@/components/ui/ScopedLink'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTeamValueMetrics } from '@/hooks/useTeamValueMetrics'
import { formatNumber } from '@/utils/formatters'
import type { TeamValueRow } from '@/types/valueMetrics'

export const UNASSIGNED_TEAM = 'Unassigned'
export const BY_TEAM_UNAVAILABLE = 'Value by team is not available'
export const BY_TEAM_PICK_PROJECT = 'Pick a project to see value by team'
export const NO_RULES_TITLE = 'No ownership rules yet'
export const NO_RULES_ASK = 'Ask a QA lead to set up ownership rules.'

const MESSAGE = 'rounded-xl border px-4 py-6 text-center text-[12.5px] text-[var(--color-text-muted)]'
const NUM = 'px-2 py-1.5 text-right tabular-nums'

export function ownershipTeamHref(team: string): string {
  return `/ownership?team=${encodeURIComponent(team)}`
}

const hours = (h: number) => `${formatNumber(h, { maximumFractionDigits: 1 })} h`

/** The server's order (hours saved, tests, team), with Unassigned held last whatever arrives. */
export function orderTeams(teams: readonly TeamValueRow[]): TeamValueRow[] {
  const isUnassigned = (row: TeamValueRow) => row.team.trim().toLowerCase() === UNASSIGNED_TEAM.toLowerCase()
  return [...teams.filter((row) => !isUnassigned(row)), ...teams.filter(isUnassigned)]
}

export interface ValueMetricsByTeamProps {
  projectId: string | null | undefined
  days: number
  /** QA lead and above: team names link to `/ownership?team=…`. */
  canEditOwnership: boolean
}

export default function ValueMetricsByTeam({ projectId, days, canEditOwnership }: ValueMetricsByTeamProps) {
  const pinned = Boolean(projectId)
  const { teams: answer, malformed, isLoading } = useTeamValueMetrics(projectId ?? null, days, pinned)

  if (!pinned) {
    return (
      <section aria-label="Value by team" data-value-by-team="pick-project" className={MESSAGE} style={{ borderColor: 'var(--color-border)' }}>
        {BY_TEAM_PICK_PROJECT}
      </section>
    )
  }
  if (malformed) {
    return (
      <section aria-label="Value by team" data-value-by-team="unavailable" className={MESSAGE} style={{ borderColor: 'var(--color-border)' }}>
        {BY_TEAM_UNAVAILABLE}
      </section>
    )
  }
  if (!answer) {
    return (
      <section aria-label="Value by team" data-value-by-team="loading" className="flex justify-center py-16">
        {isLoading ? <LoadingSpinner size="lg" /> : null}
      </section>
    )
  }
  if (!answer.rules_configured) {
    return (
      <section aria-label="Value by team" data-value-by-team="no-rules" className={MESSAGE} style={{ borderColor: 'var(--color-border)' }}>
        <p className="m-0 font-semibold text-[var(--color-text)]">{NO_RULES_TITLE}</p>
        {canEditOwnership ? (
          <ScopedLink to="/ownership" containerClassName="mt-2 inline-block" className="text-[var(--color-accent)] hover:underline">
            Set up ownership
          </ScopedLink>
        ) : (
          <p className="m-0 mt-1">{NO_RULES_ASK}</p>
        )}
      </section>
    )
  }

  const rows = orderTeams(answer.teams)
  return (
    <section
      aria-label="Value by team"
      data-value-by-team="drawn"
      data-primary=""
      className="rounded-xl"
      style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', padding: '14px 16px' }}
    >
      <h2 className="m-0 mb-2 text-[13px] font-semibold text-[var(--color-text)]">Value by team · last {answer.window_days || days} days</h2>
      {rows.length === 0 ? (
        <p className="m-0 py-4 text-center text-[12.5px] text-[var(--color-text-muted)]">No team's tests ran in this window.</p>
      ) : (
        <table className="w-full text-[12.5px] text-[var(--color-text)]" style={{ tableLayout: 'fixed', borderCollapse: 'collapse' }}>
          <colgroup>
            {/* The team column takes what is left (>= 160 px); six 96 px number columns. */}
            <col style={{ minWidth: 160 }} />
            {Array.from({ length: 6 }, (_, i) => (
              <col key={i} style={{ width: 96 }} />
            ))}
          </colgroup>
          <thead>
            <tr className="text-[11px] uppercase text-[var(--color-text-muted)]" style={{ borderBottom: '1px solid var(--color-border)' }}>
              <th scope="col" className="px-2 py-1.5 text-left font-medium">Team</th>
              <th scope="col" className={`${NUM} font-medium`}>Tests</th>
              <th scope="col" className={`${NUM} font-medium`}>Pass rate</th>
              <th scope="col" className={`${NUM} font-medium`}>Flaky</th>
              <th scope="col" className={`${NUM} font-medium`}>Defects</th>
              <th scope="col" className={`${NUM} font-medium`}>MTTR</th>
              <th scope="col" className={`${NUM} font-medium`}>Hours saved</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const unassigned = row.team.trim().toLowerCase() === UNASSIGNED_TEAM.toLowerCase()
              return (
                <tr key={row.team} data-team-row={row.team} style={{ borderBottom: '1px dashed var(--color-border)' }}>
                  <td className="truncate px-2 py-1.5" title={row.team}>
                    {canEditOwnership && !unassigned ? (
                      <ScopedLink to={ownershipTeamHref(row.team)} className="text-[var(--color-accent)] hover:underline" hintInTitleOnly>
                        {row.team}
                      </ScopedLink>
                    ) : (
                      <span className={unassigned ? 'italic text-[var(--color-text-muted)]' : undefined}>{row.team}</span>
                    )}
                  </td>
                  <td className={NUM}>{formatNumber(row.tests)}</td>
                  <td className={NUM} title={row.pass_rate === null ? 'Not measured: nothing was evaluated' : undefined}>
                    {row.pass_rate === null ? '—' : `${formatNumber(row.pass_rate, { maximumFractionDigits: 1 })}%`}
                  </td>
                  <td className={NUM}>{formatNumber(row.flaky_tests)}</td>
                  <td className={NUM}>{formatNumber(row.defects)}</td>
                  <td className={NUM} title={row.mttr_hours === null ? 'Not measured: no defect was resolved' : undefined}>
                    {row.mttr_hours === null ? '—' : hours(row.mttr_hours)}
                  </td>
                  <td
                    className={NUM}
                    title={`Triage ${hours(row.hours.triage)} · duplicates absorbed ${hours(row.hours.dedup)}`}
                  >
                    {hours(row.hours_saved)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      <div data-value-by-team-totals="" className="mt-3 flex flex-col gap-0.5 text-[11.5px] text-[var(--color-text-secondary)]">
        <span>
          Not attributable to a team: {hours(answer.not_attributable_hours)} (runs unblocked by quarantine are counted per run)
        </span>
        <span>
          Total {hours(answer.headline_hours)} — the headline for these {answer.window_days || days} days
        </span>
      </div>
    </section>
  )
}
