import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { failureGroupsSeries, HOSTILE_GROUP_LABELS } from './failureGroups.fixtures'
import { failureGroupsModel, type FailureGroup } from './failureGroups.model'
import FailureGroupPanel from './FailureGroupPanel'
import FailureGroupTable, { DROPPED_COLUMNS_NOTE, TABLE_FIRST_ROWS } from './FailureGroupTable'

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const model = failureGroupsModel(failureGroupsSeries({ groups: 14, hostile: true }))

describe('FailureGroupTable', () => {
  it('lists the ten largest, in rank order, with every column', () => {
    render(<FailureGroupTable groups={model.groups} trendGrain="day" onOpen={vi.fn()} />)
    const table = screen.getByRole('table', { name: 'Failure groups, largest first' })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['Rank', 'Failure group', 'Failures', 'Share', 'Tests', 'Runs', 'First seen', 'Last seen', 'Category', 'Trend'])
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(TABLE_FIRST_ROWS)
    expect(
      within(rows[0])
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['1', '400', expect.stringMatching(/%$/), '57', '133', '2026-09-01', '2026-09-07', 'Product bug', ''])
    expect(within(rows[0]).getByRole('img', { name: /^Failures per day, group 1/ })).toBeInTheDocument()
  })

  it('names are buttons that open the group (the keyboard path), hostile text kept as text', () => {
    const onOpen = vi.fn<(group: FailureGroup) => void>()
    const { container } = render(
      <FailureGroupTable groups={model.groups} trendGrain="day" onOpen={onOpen} selectedId={model.groups[1].id} />,
    )
    const button = screen.getByRole('button', { name: HOSTILE_GROUP_LABELS[0] })
    fireEvent.click(button)
    expect(onOpen).toHaveBeenCalledWith(model.groups[0])
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('button', { name: '__proto__' })).toHaveClass('font-semibold')
    expect(button).not.toHaveClass('font-semibold')
    expect(button).toHaveStyle({ overflowWrap: 'anywhere' })
  })

  // The console theme's accent is about 4:1 on its card (axe color-contrast, I-G gallery run): the names use the
  // accent's INK token, which every theme sets for text on a card.
  it('draws the group names in the accent ink colour, not the raw accent', () => {
    const { container } = render(<FailureGroupTable groups={model.groups} trendGrain="day" onOpen={vi.fn()} />)
    const names = [...container.querySelectorAll('[data-group-open]')]
    expect(names.length).toBeGreaterThan(0)
    for (const name of names) {
      expect(name.className).toContain('text-[var(--color-accent-ink)]')
      expect(name.className).not.toContain('text-[var(--color-accent)]')
    }
  })

  it('"Show all" lists the rest and can fold back', () => {
    render(<FailureGroupTable groups={model.groups} trendGrain="week" onOpen={vi.fn()} />)
    const toggle = screen.getByRole('button', { name: 'Show all 14 groups (4 more)' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(screen.getAllByRole('row')).toHaveLength(15)
    fireEvent.click(screen.getByRole('button', { name: 'Show the 10 largest' }))
    expect(screen.getAllByRole('row')).toHaveLength(11)
  })

  it('a figure the server did not send is "—", never 0; a one-point trend is not a line', () => {
    const bare: FailureGroup = {
      ...model.groups[0],
      affectedTests: null,
      share: null,
      firstSeen: null,
      trend: [{ x: '2026-09-01', y: 3 }],
    }
    render(<FailureGroupTable groups={[bare]} trendGrain={null} onOpen={vi.fn()} />)
    const cells = within(screen.getAllByRole('row')[1])
      .getAllByRole('cell')
      .map((c) => c.textContent)
    expect(cells[2]).toBe('—')
    expect(cells[3]).toBe('—')
    expect(cells[5]).toBe('—')
    expect(cells[8]).toBe('—Failures per day: not enough points')
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
  })

  // R2-B F-05: at 375/640/768 the ten columns (760 px; 838 in DejaVu) scrolled sideways with no cue.
  describe('a narrow frame keeps fewer columns; the rest are in the group’s panel', () => {
    const headers = () =>
      within(screen.getByRole('table'))
        .getAllByRole('columnheader')
        .map((h) => h.textContent)

    it('below 768 px: rank, group, failures, share and the category (the plot’s colour key)', () => {
      const { container } = render(<FailureGroupTable groups={model.groups} trendGrain="day" onOpen={vi.fn()} width={640} />)
      expect(headers()).toEqual(['Rank', 'Failure group', 'Failures', 'Share', 'Category'])
      const first = within(screen.getAllByRole('row')[1])
      expect(first.getByRole('rowheader')).toHaveTextContent(new RegExp(`^${escape(HOSTILE_GROUP_LABELS[0])}$`))
      expect(first.getAllByRole('cell').map((c) => c.textContent)).toEqual(['1', '400', expect.stringMatching(/%$/), 'Product bug'])
      expect(container.querySelector('[data-group-table-note]')).toHaveTextContent(
        'Open a group for its tests, runs, first and last seen, and its trend.',
      )
      expect(container.querySelector('[data-group-table]')).toHaveAttribute('data-columns', 'compact')
    })

    it('below 480 px: rank, group (its category under the name), failures, share', () => {
      const { container } = render(<FailureGroupTable groups={model.groups} trendGrain="day" onOpen={vi.fn()} width={375} />)
      expect(headers()).toEqual(['Rank', 'Failure group', 'Failures', 'Share'])
      const first = within(screen.getAllByRole('row')[1])
      expect(first.getByRole('rowheader')).toHaveTextContent(`${HOSTILE_GROUP_LABELS[0]}Product bug`)
      expect(container.querySelector('[data-group-table-note]')).toHaveTextContent(DROPPED_COLUMNS_NOTE)
    })

    it('768 px and wider, or not measured yet: every column, no note', () => {
      for (const width of [768, undefined]) {
        const { container, unmount } = render(
          <FailureGroupTable groups={model.groups} trendGrain="day" onOpen={vi.fn()} width={width} />,
        )
        expect(headers()).toHaveLength(10)
        expect(container.querySelector('[data-group-table-note]')).toBeNull()
        unmount()
      }
    })
  })
})

describe('FailureGroupPanel', () => {
  it('closed: nothing', () => {
    const { container } = render(<FailureGroupPanel group={null} trendGrain="day" onClose={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the group by rank, shows its line, figures, categories, top tests and signature as text', () => {
    const onViewRows = vi.fn()
    const onClose = vi.fn()
    render(<FailureGroupPanel group={model.groups[0]} trendGrain="day" onClose={onClose} onViewRows={onViewRows} />)
    const panel = screen.getByRole('complementary', { name: 'Failure group #1' })
    expect(within(panel).getByText(HOSTILE_GROUP_LABELS[0])).toBeInTheDocument()
    expect(panel.querySelector('img')).toBeNull()
    expect(panel.querySelector('script')).toBeNull()
    expect(panel).toHaveTextContent('2 different first lines share this signature: two causes may have been grouped together.')
    expect(panel).toHaveTextContent('Failures400')
    expect(panel).toHaveTextContent('First seen (UTC)2026-09-01')
    expect(within(panel).getByRole('img', { name: /^Failures per day/ })).toBeInTheDocument()
    expect(panel.querySelectorAll('[data-group-top-test]')).toHaveLength(2)
    expect(panel.querySelector('[data-group-top-test]')).toHaveTextContent('<script>window.__xss=1</script>test_a (200)')
    expect(panel.querySelector('[data-group-signature]')).toHaveTextContent(model.groups[0].id)
    fireEvent.click(screen.getByRole('button', { name: 'View rows' }))
    expect(onViewRows).toHaveBeenCalledWith(model.groups[0])
  })

  it('a top test with no name is named by its fingerprint', () => {
    const group: FailureGroup = { ...model.groups[1], topTests: [{ fingerprint: 'fp-x', projectId: null, name: '', count: 3 }] }
    render(<FailureGroupPanel group={group} trendGrain="day" onClose={vi.fn()} />)
    expect(document.querySelector('[data-group-top-test]')).toHaveTextContent('fp-x (3)')
  })

  it('no "View rows" without somewhere to open them; a single-line group has no collision note', () => {
    render(<FailureGroupPanel group={model.groups[1]} trendGrain="day" onClose={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'View rows' })).toBeNull()
    expect(screen.getByRole('complementary')).not.toHaveTextContent('different first lines')
  })

  it('a group with no categories, tests or trend shows none of those sections', () => {
    const bare: FailureGroup = { ...model.groups[1], categories: [], topTests: [], trend: [], distinctRawLines: null }
    render(<FailureGroupPanel group={bare} trendGrain="week" onClose={vi.fn()} />)
    const panel = screen.getByRole('complementary')
    expect(within(panel).queryByRole('heading', { name: 'Categories' })).toBeNull()
    expect(within(panel).queryByRole('heading', { name: 'Tests it fails most' })).toBeNull()
    expect(within(panel).queryByRole('heading', { name: 'Failures per week' })).toBeNull()
  })
})
