/**
 * W08 TRND-08: `ValueMetricsByTeam` over the REAL hook and guard, with only
 * the service mocked: what a garbage answer does, the order of the rows, the
 * links, and that the totals line says what the table adds up to.
 */
import { createElement } from 'react'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getByTeam = vi.hoisted(() => vi.fn())
vi.mock('@/services/teamValueMetricsService', () => ({ teamValueMetricsService: { getByTeam } }))

import ValueMetricsByTeam, { orderTeams, ownershipTeamHref } from './ValueMetricsByTeam'

function renderView(props: Partial<Parameters<typeof ValueMetricsByTeam>[0]> = {}) {
  return render(
    createElement(
      MemoryRouter,
      null,
      createElement(
        SWRConfig,
        { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } },
        createElement(ValueMetricsByTeam, { projectId: 'proj-1', days: 30, canEditOwnership: true, ...props }),
      ),
    ),
  )
}

const team = (name: string, hours: number) => ({
  team: name,
  tests: 3,
  executions: 9,
  evaluated: 9,
  passed: 9,
  pass_rate: 100,
  flaky_tests: 0,
  defects: 0,
  mttr_hours: null,
  hours_saved: hours,
  hours: { triage: hours, dedup: 0 },
})

describe('ValueMetricsByTeam', () => {
  beforeEach(() => getByTeam.mockReset())

  it.each([
    ['undefined', undefined],
    ['null', null],
    ['{}', {}],
    ['a wrong-typed teams', { measured: true, rules_configured: true, teams: 'x', headline_hours: 1, not_attributable_hours: 0 }],
  ])('a %s answer says "Value by team is not available" (no crash)', async (_label, answer) => {
    getByTeam.mockResolvedValue(answer)
    renderView()
    expect(await screen.findByText('Value by team is not available')).toBeInTheDocument()
  })

  it('draws the valid answer, Unassigned last, with the totals line', async () => {
    getByTeam.mockResolvedValue({
      window_days: 30,
      measured: true,
      reason: null,
      rules_configured: true,
      teams: [team('Unassigned', 0.5), team('Search', 2.25)],
      not_attributable_hours: 1,
      headline_hours: 3.75,
    })
    renderView()
    const table = await screen.findByRole('table')
    const names = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0].textContent)
    expect(names).toEqual(['Search', 'Unassigned'])
    expect(screen.getByText('Total 3.8 h — the headline for these 30 days')).toBeInTheDocument()
    expect(getByTeam).toHaveBeenCalledWith('proj-1', 30)
  })

  it('asks nothing without a project and says to pick one', () => {
    renderView({ projectId: null })
    expect(screen.getByText('Pick a project to see value by team')).toBeInTheDocument()
    expect(getByTeam).not.toHaveBeenCalled()
  })

  it('orderTeams keeps the server order and moves Unassigned (any case) last', () => {
    const rows = [team('unassigned', 1), team('B', 2), team('A', 3)]
    expect(orderTeams(rows).map((r) => r.team)).toEqual(['B', 'A', 'unassigned'])
  })

  it('ownershipTeamHref encodes the team name', () => {
    expect(ownershipTeamHref('a&b #c')).toBe('/ownership?team=a%26b%20%23c')
    expect(new URLSearchParams(ownershipTeamHref('a&b #c').split('?')[1]).get('team')).toBe('a&b #c')
  })
})
