import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import Sidebar from './Sidebar'

// Mock usePermissions to control what the Sidebar renders
const mockPermissions = {
  role: 'ADMIN' as const,
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

// Control the rollout flags per key (manual_upload MRU-17, ask_ai_chat US-2.1).
const mockFlags = vi.hoisted(() => ({ byKey: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => mockFlags.byKey[key] ?? false,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))

// Control the AI analysis mode (the Ask AI entry hides in rules mode).
const mockAI = vi.hoisted(() => ({ config: undefined as { analysis_mode: string } | undefined }))
vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: () => ({ data: mockAI.config }),
}))

// Sidebar source as text, via Vite. `node:fs` would type-check under vitest and
// then fail `npm run build` (which is `tsc && vite build`), because tsconfig
// sets types: ["vite/client"] and does not pull in @types/node — the same trap
// documented in routeScope.ratchet.test.ts.
const SIDEBAR_SOURCE: string = Object.values(
  import.meta.glob('./Sidebar.tsx', { query: '?raw', import: 'default', eager: true }),
)[0] as string

describe('Sidebar', () => {
  beforeEach(() => {
    mockPermissions.role = 'ADMIN'
    mockPermissions.canAccessManagement = true
    mockPermissions.canViewSettings = true
    mockFlags.byKey = {}
    mockAI.config = undefined
  })

  it('hides Upload Report when the manual_upload flag is off, shows it when on', () => {
    // Render within a Testing route so that group is expanded (children render).
    const { rerender } = render(
      <MemoryRouter initialEntries={['/runs']}><Sidebar /></MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: /Coverage/ })).toBeInTheDocument()  // group is open
    expect(screen.queryByRole('link', { name: /Upload Report/ })).not.toBeInTheDocument()

    mockFlags.byKey = { manual_upload: true }
    rerender(<MemoryRouter initialEntries={['/runs']}><Sidebar /></MemoryRouter>)
    expect(screen.getByRole('link', { name: /Upload Report/ })).toBeInTheDocument()
  })

  it('shows Ask AI only when the ask_ai_chat flag is on AND the AI mode is not rules', () => {
    // Render within an AI Reports route so that group is expanded.
    const at = ['/agents']
    const { rerender } = render(
      <MemoryRouter initialEntries={at}><Sidebar /></MemoryRouter>,
    )
    // Flag off → hidden regardless of mode.
    expect(screen.getByRole('link', { name: /AI Pipeline/ })).toBeInTheDocument() // group open
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    // Flag on but rules mode → still hidden (nothing to chat with).
    mockFlags.byKey = { ask_ai_chat: true }
    mockAI.config = { analysis_mode: 'rules' }
    rerender(<MemoryRouter initialEntries={at}><Sidebar /></MemoryRouter>)
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    // Flag on but AI config not yet loaded → hidden (no flicker of a dead link).
    mockAI.config = undefined
    rerender(<MemoryRouter initialEntries={at}><Sidebar /></MemoryRouter>)
    expect(screen.queryByRole('link', { name: /Ask AI/ })).not.toBeInTheDocument()

    // Flag on + LLM-capable mode → visible.
    mockAI.config = { analysis_mode: 'auto' }
    rerender(<MemoryRouter initialEntries={at}><Sidebar /></MemoryRouter>)
    expect(screen.getByRole('link', { name: /Ask AI/ })).toBeInTheDocument()
  })

  it('renders branding and top-level group links', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    // Group headers are always visible
    expect(screen.getByRole('link', { name: /Dashboard/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Testing/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /AI Reports/ })).toBeInTheDocument()
  })

  it('shows Management group for admin/QA Lead users', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: /Management/ })).toBeInTheDocument()
  })

  it('shows Settings link for admin/QA Lead users', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument()
  })

  it('hides Management group for VIEWER role', () => {
    mockPermissions.canAccessManagement = false

    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('link', { name: /Management/ })).not.toBeInTheDocument()
  })

  it('hides Settings link for non-management roles', () => {
    mockPermissions.canAccessManagement = false

    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument()
  })
  // The e2e suite anchors ~38 'the app shell rendered' assertions on this
  // landmark. It replaced a bare locator('aside'), which was a strict-mode
  // violation on every page carrying a second <aside> (LiveExecutionPage's
  // 'Pipeline events' panel, ChatPage's conversation list) — so those tests
  // passed only while the page under test happened to be empty. Dropping the
  // accessible name here would break all of them at once, far from the cause.
  it('exposes the primary nav as a landmark with a stable accessible name', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
  })
})

describe('Sidebar — a group must claim the routes of its own children', () => {
  /**
   * A group expands and highlights when `location.pathname` starts with one of
   * its `activePrefixes` (see `isWithinGroup`). Adding a child to `children`
   * without adding its route to `activePrefixes` produces a nav that is silent
   * about where you are: land on the page and its own group stays collapsed,
   * so the entry you just used is not even on screen.
   *
   * This is exactly what happened when `/activity` was added to the Testing
   * group. Read from the component SOURCE rather than an exported constant,
   * because the nav config is module-private and exporting it purely for a
   * test would let the two drift apart in a different way.
   */
  const source = SIDEBAR_SOURCE

  const groups = [...source.matchAll(/activePrefixes:\s*\[([^\]]*)\][\s\S]*?children:\s*\[([\s\S]*?)\n\s{2,4}\]/g)]

  it('parses at least three groups out of the sidebar source', () => {
    // Without this the loop below could iterate zero times and pass vacuously.
    expect(groups.length).toBeGreaterThanOrEqual(3)
  })

  it('covers every child route with one of its own activePrefixes', () => {
    const uncovered: string[] = []

    for (const [, prefixBlob, childBlob] of groups) {
      const prefixes = [...prefixBlob.matchAll(/'([^']+)'/g)].map(m => m[1])
      const childRoutes = [...childBlob.matchAll(/to:\s*'([^']+)'/g)].map(m => m[1])

      for (const route of childRoutes) {
        // Compare on the path only — `/runs?upload=1` is the `/runs` page.
        const path = route.split('?')[0]
        if (!prefixes.some(p => path.startsWith(p))) {
          uncovered.push(`${route} (prefixes: ${prefixes.join(', ')})`)
        }
      }
    }

    expect(
      uncovered,
      'these nav children are not claimed by their group\'s activePrefixes, so ' +
        'the group stays collapsed on the very page the entry links to',
    ).toEqual([])
  })
})
