import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import Sidebar, { SIDEBAR_COLLAPSED_KEY } from './Sidebar'

// Mock usePermissions to control what the Sidebar renders
const mockPermissions = {
  role: 'ADMIN' as string,
  isAdmin: true,
  isQaLead: true,
  isQaEngineer: true,
  canManageUsers: true,
  canManageProjectMembers: true,
  canGenerateApiKeys: true,
  canTriggerLlm: true,
  canAccessManagement: true,
  canViewSettings: true,
  canEditSettings: true,
  hasRole: () => true,
}

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => mockPermissions,
}))

// Control the rollout flags per key (ask_ai_chat US-2.1).
const mockFlags = vi.hoisted(() => ({ byKey: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => mockFlags.byKey[key] ?? false,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))

// Control the AI analysis mode (the Ask AI entry hides in rules mode).
const mockAI = vi.hoisted(() => ({ config: undefined as { analysis_mode: string } | undefined }))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  // The real gate: the sidebar, the page and the server agree on it.
  isLLMAvailable: (await importOriginal<typeof import('@/hooks/useAIConfig')>()).isLLMAvailable,
  useAIConfig: () => ({ data: mockAI.config }),
}))

const mockCounts = vi.hoisted(() => ({ inbox: 0, live: 0 }))
vi.mock('@/hooks/useMyFailuresCountUnscoped', () => ({
  useMyFailuresCountUnscoped: () => ({ data: { count: mockCounts.inbox } }),
}))
vi.mock('@/hooks/useLiveRunningCount', () => ({
  useLiveRunningCount: () => mockCounts.live,
}))

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar />
    </MemoryRouter>,
  )
}

function navIds(): string[] {
  return [...document.querySelectorAll('[data-nav-id]')].map((el) => el.getAttribute('data-nav-id') ?? '')
}

function activeId(): string | null {
  return document.querySelector('[data-nav-id][aria-current="page"]')?.getAttribute('data-nav-id') ?? null
}

describe('Sidebar (UX redesign P1): flat items from navConfig', () => {
  beforeEach(() => {
    mockPermissions.role = 'ADMIN'
    mockPermissions.canAccessManagement = true
    mockFlags.byKey = {}
    mockAI.config = undefined
    mockCounts.inbox = 0
    mockCounts.live = 0
    window.localStorage.removeItem(SIDEBAR_COLLAPSED_KEY)
  })

  it('a viewer sees 10 items (11 with Ask AI), no Releases and no Admin', () => {
    mockPermissions.role = 'VIEWER'
    mockPermissions.canAccessManagement = false
    renderAt('/overview')
    expect(navIds()).toEqual([
      'home', 'inbox', 'runs', 'failures', 'flaky', 'trends', 'suites', 'test-cases', 'reports', 'release-gate',
    ])
    expect(screen.queryByRole('link', { name: /Admin/ })).not.toBeInTheDocument()
  })

  it('a QA lead or admin sees Releases in RELEASE and Admin in the footer: 12, 13 with Ask AI', () => {
    mockFlags.byKey = { ask_ai_chat: true }
    mockAI.config = { analysis_mode: 'auto' }
    renderAt('/overview')
    expect(navIds()).toEqual([
      'home', 'inbox', 'runs', 'failures', 'flaky', 'trends', 'suites', 'test-cases', 'reports',
      'release-gate', 'releases', 'ask-ai', 'admin',
    ])
    expect(screen.getByRole('link', { name: /Admin/ })).toHaveAttribute('href', '/settings')
  })

  it('section labels are text, not links', () => {
    renderAt('/overview')
    const labels = [...document.querySelectorAll('[data-nav-section]')].map((el) => el.textContent)
    expect(labels).toEqual(['INVESTIGATE', 'QUALITY', 'RELEASE'])
    for (const el of document.querySelectorAll('[data-nav-section]')) expect(el.closest('a')).toBeNull()
  })

  it('shows Ask AI only when the ask_ai_chat flag is on AND the AI mode is LLM or Auto', () => {
    const { rerender } = renderAt('/overview')
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    mockFlags.byKey = { ask_ai_chat: true }
    mockAI.config = { analysis_mode: 'rules' }
    rerender(<MemoryRouter initialEntries={['/overview']}><Sidebar /></MemoryRouter>)
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    // ML mode: chat refuses (it needs an LLM), so the entry must not lead there.
    mockAI.config = { analysis_mode: 'ml' }
    rerender(<MemoryRouter initialEntries={['/overview']}><Sidebar /></MemoryRouter>)
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    mockAI.config = undefined
    rerender(<MemoryRouter initialEntries={['/overview']}><Sidebar /></MemoryRouter>)
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    mockAI.config = { analysis_mode: 'auto' }
    rerender(<MemoryRouter initialEntries={['/overview']}><Sidebar /></MemoryRouter>)
    expect(screen.getByRole('link', { name: /Ask AI/ })).toHaveAttribute('href', '/chat')
  })

  it.each([
    ['/overview', 'home'],
    ['/my-failures', 'inbox'],
    ['/reviews', 'inbox'],
    ['/runs', 'runs'],
    ['/runs/r1', 'runs'],
    ['/runs/r1/intelligence', 'runs'],
    ['/runs/compare', 'runs'],
    ['/live', 'runs'],
    ['/intelligence', 'runs'],
    ['/failures', 'failures'],
    ['/defects', 'failures'],
    ['/deep-investigate/r1', 'failures'],
    ['/flaky-coach', 'flaky'],
    ['/quarantine', 'flaky'],
    ['/trends', 'trends'],
    ['/coverage', 'trends'],
    ['/explore', 'trends'],
    ['/coverage/suite', 'suites'],
    ['/suites/s1', 'suites'],
    ['/canonical-test-cases/x', 'suites'],
    ['/test-management', 'test-cases'],
    ['/reports/summary', 'reports'],
    ['/value-metrics', 'reports'],
    ['/release-gate/r1', 'release-gate'],
    ['/releases/rel1', 'releases'],
    ['/policies/p1', 'releases'],
    ['/settings/ai', 'admin'],
    ['/projects', 'admin'],
    ['/activity', 'admin'],
    ['/agents/workflows', 'admin'],
  ])('%s highlights %s, and only it', (path, id) => {
    renderAt(path)
    expect(activeId()).toBe(id)
    expect(document.querySelectorAll('[data-nav-id][aria-current="page"]')).toHaveLength(1)
  })

  it.each(['/search', '/docs', '/getting-started', '/settings/profile', '/settings/my-notifications'])(
    '%s highlights nothing: it is not a sidebar place',
    (path) => {
      renderAt(path)
      expect(activeId()).toBeNull()
    },
  )

  it('the Inbox keeps its count badge', () => {
    mockCounts.inbox = 7
    renderAt('/overview')
    const inbox = document.querySelector('[data-nav-id="inbox"]') as HTMLElement
    expect(within(inbox).getByLabelText('7 assigned failures')).toHaveTextContent('7')
  })

  it('Runs shows a live dot while a run is reporting, and none otherwise', () => {
    const { rerender } = renderAt('/overview')
    expect(document.querySelector('[data-nav-live]')).toBeNull()
    mockCounts.live = 2
    rerender(<MemoryRouter initialEntries={['/overview']}><Sidebar /></MemoryRouter>)
    const runs = document.querySelector('[data-nav-id="runs"]') as HTMLElement
    expect(within(runs).getByRole('img', { name: '2 runs live now' })).toBeInTheDocument()
  })

  it('collapses to the icon rail, labels as tooltips, and remembers it', () => {
    const { unmount } = renderAt('/runs')
    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    const aside = document.querySelector('aside') as HTMLElement
    expect(aside).toHaveAttribute('data-sidebar', 'collapsed')
    expect(aside.className).toContain('w-16')
    const runs = document.querySelector('[data-nav-id="runs"]') as HTMLElement
    expect(runs).toHaveAttribute('title', 'Runs')
    expect(runs).toHaveAccessibleName('Runs')
    expect(runs.textContent).toBe('')
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe('1')
    unmount()

    renderAt('/runs')
    expect(document.querySelector('aside')).toHaveAttribute('data-sidebar', 'collapsed')
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(document.querySelector('aside')).toHaveAttribute('data-sidebar', 'expanded')
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBeNull()
  })

  it('the footer has no profile, settings, presentation or version rows any more', () => {
    renderAt('/overview')
    expect(screen.queryByRole('link', { name: /My Profile/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Presentation mode/ })).not.toBeInTheDocument()
    expect(document.querySelector('[data-testid="app-version-badge"]')).toBeNull()
  })

  // The e2e suite anchors ~38 'the app shell rendered' assertions on this
  // landmark (a bare locator('aside') was ambiguous beside the Live page's
  // 'Pipeline events' panel).
  it('exposes the primary nav as a landmark with a stable accessible name', () => {
    renderAt('/overview')
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })
})
