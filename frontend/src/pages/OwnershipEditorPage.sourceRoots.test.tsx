/**
 * FAIL-08 "Java source roots" on /ownership (contract §2.8 frontend 9-11).
 *
 * The real `useSourceRoots` hook and `SourceRootsSection` run over a mocked
 * service: a QA lead adds and removes rows, tests the EDITED list on recent
 * failures, saves it (one PUT with the whole list, then the coverage badge is
 * refreshed); a refused list shows the server's text in ONE toast; a viewer
 * sees the table and no inputs; the badge tooltip says how many roots locate
 * Java traces. Server text renders as text.
 */
import { createElement, type ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '@/store/authStore'
import OwnershipEditorPage from './OwnershipEditorPage'

const svc = vi.hoisted(() => ({
  getSourceRoots: vi.fn(),
  putSourceRoots: vi.fn(),
  previewSourceRoots: vi.fn(),
}))
vi.mock('@/services/sourceRootsService', () => svc)

const coverage = vi.hoisted(() => ({ value: null as unknown, refresh: vi.fn() }))
vi.mock('@/hooks/useOwnershipRules', () => ({
  useOwnershipRules: () => ({ rules: [], isLoading: false, isError: false, refresh: vi.fn() }),
  useCodeownersCoverage: () => ({ coverage: coverage.value, refresh: coverage.refresh }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: { activeProjectId: string }) => unknown) =>
    selector({ activeProjectId: 'proj-1' })),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const ROOTS = [
  { prefix: 'com.acme.pay', root: 'payments/src/test/java' },
  { prefix: '', root: 'src/test/java' },
]

function Fresh({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

function renderPage() {
  return render(
    <Fresh>
      <MemoryRouter>
        <OwnershipEditorPage />
      </MemoryRouter>
    </Fresh>,
  )
}

function section(): HTMLElement {
  return screen.getByRole('region', { name: 'Java source roots' })
}

function rows(): string[] {
  return Array.from(section().querySelectorAll('[data-source-root-row]')).map(r => (r.textContent || '').trim())
}

function addRoot(prefix: string, root: string) {
  const s = within(section())
  fireEvent.change(s.getByLabelText('Package prefix (optional)'), { target: { value: prefix } })
  fireEvent.change(s.getByLabelText('Source root'), { target: { value: root } })
  fireEvent.click(s.getByRole('button', { name: 'Add' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  coverage.value = null
  useAuthStore.setState({ user: { id: 'lead', role: 'QA_LEAD' } as never })
})
afterEach(() => {
  useAuthStore.setState({ user: null } as never)
})

describe('Java source roots — a QA lead', () => {
  it('lists the saved roots, "any package" for an empty prefix', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 20 })
    renderPage()
    await waitFor(() => expect(rows()).toHaveLength(2))
    expect(rows()[0]).toContain('com.acme.pay')
    expect(rows()[0]).toContain('payments/src/test/java')
    expect(rows()[1]).toContain('any package')
    expect(section()).toHaveTextContent(
      'Java stack traces name a class and a file, not a path. Tell TestLookup where your test sources live and Java failures are routed by path like any other.',
    )
  })

  it('adds and removes rows, then Save sends the whole list once and refreshes the badge', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    svc.putSourceRoots.mockImplementation(async (_pid: string, roots: unknown) => ({ roots, max_roots: 20 }))
    renderPage()
    await waitFor(() => expect(svc.getSourceRoots).toHaveBeenCalledTimes(1))
    const save = within(section()).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()                       // nothing to save yet

    addRoot(' com.acme.pay ', 'payments/src/test/java')
    addRoot('', 'src/test/java')
    addRoot('org.tmp', 'tmp/src')
    expect(rows()).toHaveLength(3)
    fireEvent.click(within(section()).getByRole('button', { name: 'Remove source root tmp/src' }))
    expect(rows()).toHaveLength(2)

    fireEvent.click(save)
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Java source roots saved'))
    expect(svc.putSourceRoots).toHaveBeenCalledTimes(1)
    expect(svc.putSourceRoots).toHaveBeenCalledWith('proj-1', ROOTS)
    expect(coverage.refresh).toHaveBeenCalledTimes(1)
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('tests the EDITED list on recent failures and renders the counts and examples as text', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    svc.previewSourceRoots.mockResolvedValue({
      sampled: 20, java_frames: 15, located: 12, matched: 9, lookback_days: 30,
      examples: [{ test_name: '<img src=x onerror=alert(1)>', path: 'src/test/java/com/acme/A.java' }],
    })
    renderPage()
    await waitFor(() => expect(svc.getSourceRoots).toHaveBeenCalledTimes(1))
    addRoot('', 'src/test/java')
    fireEvent.click(within(section()).getByRole('button', { name: 'Test on recent failures' }))

    await waitFor(() => expect(section().querySelector('[data-source-roots-preview]')).not.toBeNull())
    expect(svc.previewSourceRoots).toHaveBeenCalledWith('proj-1', [{ prefix: '', root: 'src/test/java' }])
    expect(section()).toHaveTextContent(
      '12 of 15 recent Java failures map to a path; 9 match a path rule (last 30 days).',
    )
    expect(section()).toHaveTextContent('<img src=x onerror=alert(1)> → src/test/java/com/acme/A.java')
    expect(section().querySelector('img')).toBeNull()
    expect(svc.putSourceRoots).not.toHaveBeenCalled()

    // Editing again drops the stale preview.
    addRoot('com.acme', 'core/src')
    expect(section().querySelector('[data-source-roots-preview]')).toBeNull()
  })

  it('says so when no recent failure has a Java trace', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 20 })
    svc.previewSourceRoots.mockResolvedValue({
      sampled: 4, java_frames: 0, located: 0, matched: 0, lookback_days: 30, examples: [],
    })
    renderPage()
    await waitFor(() => expect(rows()).toHaveLength(2))
    fireEvent.click(within(section()).getByRole('button', { name: 'Test on recent failures' }))
    await waitFor(() => expect(section()).toHaveTextContent('No recent failure has a Java stack trace.'))
  })

  it('a refused list shows the server text in ONE toast and keeps the edit', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    const detail = 'Source root "../x" must be a relative path inside the repository (letters, digits, . _ - /; no ..).'
    svc.putSourceRoots.mockRejectedValue({ response: { status: 422, data: { detail } } })
    renderPage()
    await waitFor(() => expect(svc.getSourceRoots).toHaveBeenCalledTimes(1))
    addRoot('', '../x')
    fireEvent.click(within(section()).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(toast.error).toHaveBeenCalledWith(detail)
    expect(toast.success).not.toHaveBeenCalled()
    expect(coverage.refresh).not.toHaveBeenCalled()
    expect(rows()).toHaveLength(1)
  })

  it('Add stays disabled at the cap', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 2 })
    renderPage()
    await waitFor(() => expect(rows()).toHaveLength(2))
    fireEvent.change(within(section()).getByLabelText('Source root'), { target: { value: 'more/src' } })
    expect(within(section()).getByRole('button', { name: 'Add' })).toBeDisabled()
  })
})

describe('Java source roots — below QA lead', () => {
  it('a viewer sees the table read-only: no inputs, no buttons', async () => {
    useAuthStore.setState({ user: { id: 'v', role: 'QA_ENGINEER' } as never })
    svc.getSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 20 })
    renderPage()
    await waitFor(() => expect(rows()).toHaveLength(2))
    expect(within(section()).queryAllByRole('textbox')).toHaveLength(0)
    expect(within(section()).queryAllByRole('button')).toHaveLength(0)
  })
})

describe('the coverage badge tooltip follows source_roots', () => {
  const base = {
    path_rules: 1, codeowners_rules: 1, sampled: 3, located: 0, matched: 0, coverage_pct: null, lookback_days: 30,
  }

  function badgeTitle(): string {
    const el = Array.from(document.querySelectorAll('span'))
      .find(e => (e.textContent || '').trim().startsWith('Coverage'))
    if (!el) throw new Error('coverage badge not rendered')
    return el.getAttribute('title') || ''
  }

  it('says Java traces are not located without roots', () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    coverage.value = { ...base, source_roots: 0 }
    renderPage()
    expect(badgeTitle()).toContain('Java stack traces are not located.')
  })

  it('names the root count once roots exist', () => {
    svc.getSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 20 })
    coverage.value = { ...base, source_roots: 2 }
    renderPage()
    expect(badgeTitle()).toContain('Java traces are located through 2 source root(s).')
    expect(badgeTitle()).not.toContain('Java stack traces are not located.')
  })

  it('an older server without the field reads as no roots', () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    coverage.value = { ...base }
    renderPage()
    expect(badgeTitle()).toContain('Java stack traces are not located.')
  })
})
